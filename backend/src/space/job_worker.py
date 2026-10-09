"""Supervise one isolated extraction at a time within the worker connection budget."""

import argparse
import asyncio
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from space.jobs import LEASE_SECONDS, claim, process
from space.model_provider import MemoryModels
from space.providers import LocalKeys
from space.queue_watch import QueueWatch, wait
from space.settings import CONFIG_FILE, Settings, database_connection_options
from space.workspace import Database, Workspace

JOB_DEADLINE_SECONDS = 240
EXIT_IDLE = 10
EXIT_TIMEOUT = 124
# Eligible work, read without claiming it: queued and due, or a crashed attempt's lease.
PENDING_SQL = (
    "SELECT EXISTS (SELECT 1 FROM space.memory_jobs WHERE "
    "(status='queued' AND available_at<=now()) OR "
    "(status='processing' AND lease_until<now())) AS pending"
)


def restrict_connection(connection):
    role = connection.execute(
        "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb AS privileged, "
        "pg_has_role(current_user,'space_job_worker','MEMBER') AS worker "
        "FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    connection.commit()
    if role["privileged"] or not role["worker"]:
        raise RuntimeError("Jobs require a non-privileged login with the space_job_worker role")
    connection.execute("SET ROLE space_job_worker")
    capability = connection.execute(
        "SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR "
        "rolcreatedb OR rolreplication AS privileged FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    connection.commit()
    if capability["privileged"]:
        raise RuntimeError("The space_job_worker capability role must remain non-privileged")


def run_attempt(queue_env_file=None):
    """Child-only runtime: claim, process, publish and close under the parent's deadline."""
    settings = worker_settings(queue_env_file)
    if not settings.job_database_url or not settings.database_url:
        raise RuntimeError("Configure SPACE_JOB_DATABASE_URL and SPACE_DATABASE_URL")
    # Only the child opens application connections; a looping supervisor holds at most
    # one queue connection to check for work.
    database = Database(settings, pool_size=1)
    keys = LocalKeys()
    workspace = Workspace(
        database, keys, None, settings.mcp_resource_url, settings.web_url, MemoryModels(settings)
    )
    try:
        database.open()
        database.adopt_embedding_width()
        with psycopg.connect(
            settings.job_database_url.get_secret_value(),
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
            process(workspace, queue, job)
    finally:
        asyncio.run(workspace.aclose())
        database.close()
        keys.close()
    return 0


def worker_settings(queue_env_file=None) -> Settings:
    env_files = [CONFIG_FILE]
    if queue_env_file:
        env_files.append(queue_env_file)
    return Settings(_env_file=env_files)


def supervise(command, deadline):
    """Kill and reap an overrun child even if its Python interpreter cannot respond."""
    try:
        return subprocess.run(  # noqa: S603 - fixed Python module, no shell or job input
            command,
            timeout=deadline,
            check=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
    except subprocess.TimeoutExpired:
        # subprocess.run sends SIGKILL and waits for the child before raising.
        # Its lease is left intact so commit/status crash recovery still works.
        return EXIT_TIMEOUT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    parser.add_argument("--drain", action="store_true", help="exit when no eligible jobs remain")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3,
        help="seconds between queue checks while idle when running continuously (default: 3)",
    )
    parser.add_argument("--max-jobs", type=int, default=20, help="maximum attempts in a drain run")
    parser.add_argument("--queue-env-file", type=Path, help="worker-only queue connection settings")
    parser.add_argument(
        "--job-deadline",
        type=int,
        default=JOB_DEADLINE_SECONDS,
        help="seconds per child attempt, including startup and cleanup (default: 240)",
    )
    parser.add_argument("--attempt", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.max_jobs <= 100:
        parser.error("--max-jobs must be between 1 and 100")
    if not 1 <= args.job_deadline <= LEASE_SECONDS - 30:
        parser.error(f"--job-deadline must be between 1 and {LEASE_SECONDS - 30}")
    if args.queue_env_file and not args.queue_env_file.is_file():
        parser.error("The worker queue configuration file does not exist")
    if not 0.5 <= args.poll_interval <= 300:
        parser.error("--poll-interval must be between 0.5 and 300")
    if args.attempt:
        return run_attempt(args.queue_env_file)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    command = [sys.executable, "-m", "space.job_worker", "--attempt"]
    if args.queue_env_file:
        command.extend(["--queue-env-file", str(args.queue_env_file)])
    # A continuous supervisor checks the queue with one cheap query instead of starting
    # an attempt process while idle.
    watch = None
    if not (args.once or args.drain):
        settings = worker_settings(args.queue_env_file)
        if not settings.job_database_url:
            parser.error("Configure SPACE_JOB_DATABASE_URL")
        watch = QueueWatch(
            settings.job_database_url.get_secret_value(),
            database_connection_options(settings),
            restrict_connection,
            PENDING_SQL,
        )
    attempts, failed = 0, False
    while not stopping:
        if watch is not None and not watch.pending():
            wait(args.poll_interval, lambda: stopping)
            continue
        outcome = supervise(command, args.job_deadline)
        if outcome == EXIT_IDLE:
            if args.drain:
                break
        else:
            attempts += 1
            if outcome != 0:
                failed = True
                print(
                    "Extraction attempt timed out; lease recovery remains enabled"
                    if outcome == EXIT_TIMEOUT
                    else "Extraction process failed; lease recovery remains enabled",
                    file=sys.stderr,
                )
        if args.once or (args.drain and attempts >= args.max_jobs):
            break
        if not stopping and outcome != 0:
            time.sleep(1)
    if watch is not None:
        watch.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
