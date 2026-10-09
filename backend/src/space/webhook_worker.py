"""Parameters: once, bounded drain or continuous loop execution, and worker-only settings.
What it does: supervises isolated webhook delivery attempts; the loop checks the queue
cheaply while idle and drains it when deliveries are due.
Output: an exit status; child output and secrets are never forwarded.
"""

import argparse
import asyncio
import signal
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from space.providers import LocalKeys
from space.queue_watch import QueueWatch, wait
from space.settings import CONFIG_FILE, Settings, database_connection_options
from space.webhook_delivery import claim, finish, process
from space.workspace import Database, Workspace

ATTEMPT_DEADLINE_SECONDS = 30
EXIT_IDLE = 10
EXIT_TIMEOUT = 124
# Due deliveries, read without leasing them: queued and due, or a crashed attempt's lease.
PENDING_SQL = (
    "SELECT EXISTS (SELECT 1 FROM space.webhook_deliveries WHERE "
    "(status='queued' AND available_at<=clock_timestamp()) OR "
    "(status='processing' AND lease_until<=clock_timestamp())) AS pending"
)


def restrict_connection(connection):
    """Parameters: the queue login connection before any webhook read.
    What it does: rejects privileged logins and selects only the webhook capability role.
    Output: a non-privileged space_webhook_worker connection, or a safe configuration error.
    """
    role = connection.execute(
        "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication "
        "AS privileged, pg_has_role(current_user,'space_webhook_worker','MEMBER') AS worker "
        "FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    connection.commit()
    if role is None or role["privileged"] or not role["worker"]:
        raise RuntimeError("Webhooks require a non-privileged space_webhook_worker member")
    connection.execute("SET ROLE space_webhook_worker")
    capability = connection.execute(
        "SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR "
        "rolcreatedb OR rolreplication AS privileged FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    connection.commit()
    if capability is None or capability["privileged"]:
        raise RuntimeError("The space_webhook_worker capability must remain non-privileged")


def run_attempt(queue_env_file=None):
    """Parameters: optional worker-only configuration path, supplied by the operator.
    What it does: opens fenced runtime and worker connections and completes at most one attempt.
    Output: EXIT_IDLE when no work is eligible, otherwise zero after status publication.
    """
    settings = worker_settings(queue_env_file)
    if not settings.webhook_database_url or not settings.database_url:
        raise RuntimeError("Configure SPACE_WEBHOOK_DATABASE_URL and SPACE_DATABASE_URL")
    database, keys, workspace = None, None, None
    try:
        database = Database(settings, pool_size=1)
        keys = LocalKeys()
        workspace = Workspace(database, keys, None, settings.mcp_resource_url, settings.web_url)
        database.open()
        with psycopg.connect(
            settings.webhook_database_url.get_secret_value(),
            **database_connection_options(settings),
            connect_timeout=5,
            row_factory=dict_row,
        ) as queue:
            restrict_connection(queue)
            with queue.transaction():
                environment = queue.execute(
                    "SELECT space.check_environment(%s) AS ready", (None,)
                ).fetchone()
                if not environment["ready"]:
                    return EXIT_IDLE
            job = claim(queue)
            if job is None:
                return EXIT_IDLE
            finish(queue, job, process(workspace, queue, job))
            return 0
    finally:
        try:
            if workspace is not None:
                asyncio.run(workspace.aclose())
        finally:
            try:
                if database is not None:
                    database.close()
            finally:
                if keys is not None:
                    keys.close()


def worker_settings(queue_env_file=None) -> Settings:
    env_files = [CONFIG_FILE]
    if queue_env_file:
        env_files.append(queue_env_file)
    return Settings(_env_file=env_files)


def supervise(command, deadline=ATTEMPT_DEADLINE_SECONDS):
    """Parameters: a fixed child command and the supervisor's hard attempt deadline.
    What it does: kills and reaps an overrun child, discarding every standard stream.
    Output: its exit status, or EXIT_TIMEOUT with the durable lease left for recovery.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed Python module, no shell or delivery input
            command,
            timeout=deadline,
            check=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
    except subprocess.TimeoutExpired:
        return EXIT_TIMEOUT


def main(argv=None):
    """Parameters: explicit --once, --drain or the private child --attempt arguments.
    What it does: starts only a bounded number of supervised attempts and stops at idle.
    Output: zero on success or idle, one on a safe process failure, or argparse's input error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process at most one attempt and exit")
    mode.add_argument("--drain", action="store_true", help="stop at idle or the attempt bound")
    mode.add_argument(
        "--loop", action="store_true", help="run continuously, draining whenever work is due"
    )
    mode.add_argument("--attempt", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--max-attempts", type=int, default=20, help="maximum attempts per drain run (1..100)"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3,
        help="seconds between queue checks while idle in --loop mode (default: 3)",
    )
    parser.add_argument("--queue-env-file", type=Path, help="worker-only connection settings")
    args = parser.parse_args(argv)
    if not 1 <= args.max_attempts <= 100:
        parser.error("--max-attempts must be between 1 and 100")
    if args.queue_env_file and not args.queue_env_file.is_file():
        parser.error("The worker queue configuration file does not exist")
    if not 0.5 <= args.poll_interval <= 300:
        parser.error("--poll-interval must be between 0.5 and 300")
    if args.attempt:
        try:
            return run_attempt(args.queue_env_file)
        except Exception:
            print("Webhook process unavailable; lease recovery remains enabled", file=sys.stderr)
            return 1

    stopping = False

    def stop(*_):
        """Parameters: a supervisor shutdown signal.
        What it does: lets the bounded current child complete without starting another.
        Output: an updated local stop flag.
        """
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    command = [sys.executable, "-m", "space.webhook_worker", "--attempt"]
    if args.queue_env_file:
        command.extend(["--queue-env-file", str(args.queue_env_file)])

    def drain(limit):
        failed = False
        for _ in range(limit):
            if stopping:
                break
            try:
                outcome = supervise(command, ATTEMPT_DEADLINE_SECONDS)
            except Exception:
                outcome = 1
            if outcome == EXIT_IDLE:
                break
            if outcome != 0:
                failed = True
                print(
                    "Webhook attempt timed out; lease recovery remains enabled"
                    if outcome == EXIT_TIMEOUT
                    else "Webhook process failed; lease recovery remains enabled",
                    file=sys.stderr,
                )
        return failed

    if not args.loop:
        return int(drain(1 if args.once else args.max_attempts))
    settings = worker_settings(args.queue_env_file)
    if not settings.webhook_database_url:
        parser.error("Configure SPACE_WEBHOOK_DATABASE_URL")
    watch = QueueWatch(
        settings.webhook_database_url.get_secret_value(),
        database_connection_options(settings),
        restrict_connection,
        PENDING_SQL,
    )
    try:
        while not stopping:
            if watch.pending():
                if drain(args.max_attempts):
                    wait(1, lambda: stopping)
            else:
                wait(args.poll_interval, lambda: stopping)
    finally:
        watch.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
