"""Deadline, batch lifetime and shutdown checks without real provider credentials."""

import os
import signal
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from space import job_worker


@pytest.fixture
def runtime(monkeypatch):
    settings = SimpleNamespace(
        job_database_url=SimpleNamespace(get_secret_value=lambda: "fixture"),
        database_url=True,
        database_sslmode="disable",
        database_ca_file=None,
        mcp_resource_url="https://fixture.invalid/mcp/",
        web_url="https://fixture.invalid",
    )
    database, keys, workspace, queue = (Mock() for _ in range(4))
    queue.transaction.return_value = nullcontext()
    queue.execute.return_value.fetchone.return_value = {"ready": True}
    workspace.aclose = AsyncMock()
    monkeypatch.setattr(job_worker, "Settings", lambda **_: settings)
    monkeypatch.setattr(job_worker, "Database", lambda *_, **__: database)
    monkeypatch.setattr(job_worker, "LocalKeys", lambda *_: keys)
    monkeypatch.setattr(job_worker, "MemoryModels", lambda *_: None)
    monkeypatch.setattr(job_worker, "Workspace", lambda *_: workspace)
    monkeypatch.setattr(job_worker.psycopg, "connect", lambda *_, **__: nullcontext(queue))
    monkeypatch.setattr(job_worker, "restrict_connection", Mock())
    process, claim = Mock(), Mock()
    monkeypatch.setattr(job_worker, "process", process)
    monkeypatch.setattr(job_worker, "claim", claim)
    return SimpleNamespace(
        database=database,
        keys=keys,
        workspace=workspace,
        process=process,
        claim=claim,
        queue=queue,
    )


class Watch:
    """Stands in for the idle queue check; pending answers come from a list, then True."""

    def __init__(self, answers):
        self.answers, self.closed = list(answers), False

    def pending(self):
        return self.answers.pop(0) if self.answers else True

    def close(self):
        self.closed = True


@pytest.fixture
def supervisor(monkeypatch):
    handlers = {}
    monkeypatch.setattr(
        job_worker.signal, "signal", lambda signum, handler: handlers.update({signum: handler})
    )
    run, sleep, wait = Mock(), Mock(), Mock()
    monkeypatch.setattr(job_worker, "supervise", run)
    monkeypatch.setattr(job_worker.time, "sleep", sleep)
    monkeypatch.setattr(job_worker, "wait", wait)
    monkeypatch.setattr(
        job_worker,
        "worker_settings",
        lambda *_: SimpleNamespace(
            job_database_url=SimpleNamespace(get_secret_value=lambda: "fixture"),
            database_sslmode="disable",
            database_ca_file=None,
        ),
    )
    watches = []

    def watch(*_):
        watches.append(Watch(supervisor_answers))
        return watches[-1]

    supervisor_answers = []
    monkeypatch.setattr(job_worker, "QueueWatch", watch)
    return SimpleNamespace(
        run=run,
        sleep=sleep,
        wait=wait,
        handlers=handlers,
        answers=supervisor_answers,
        watches=watches,
    )


@pytest.mark.parametrize("job", [None, {"id": 1}])
def test_child_processes_at_most_one_job_and_closes_resources(runtime, job):
    runtime.claim.return_value = job
    assert job_worker.run_attempt() == (0 if job else job_worker.EXIT_IDLE)
    runtime.claim.assert_called_once()
    assert runtime.process.call_count == bool(job)
    runtime.workspace.aclose.assert_awaited_once()
    for resource in (runtime.database, runtime.keys):
        resource.close.assert_called_once()


def test_child_error_closes_resources_without_claiming_more(runtime):
    runtime.claim.return_value = {"id": 1}
    runtime.process.side_effect = RuntimeError("fixture failure")
    with pytest.raises(RuntimeError, match="fixture failure"):
        job_worker.run_attempt()
    runtime.claim.assert_called_once()
    runtime.workspace.aclose.assert_awaited_once()
    runtime.database.close.assert_called_once()


def test_fenced_or_wrong_environment_does_not_claim_jobs(runtime):
    runtime.queue.execute.return_value.fetchone.return_value = {"ready": False}
    assert job_worker.run_attempt() == job_worker.EXIT_IDLE
    runtime.claim.assert_not_called()


@pytest.mark.parametrize(
    "outcomes,expected",
    [([job_worker.EXIT_IDLE], 1), ([0, job_worker.EXIT_IDLE], 2), ([0, 0], 2)],
)
def test_drain_exits_at_idle_or_batch_bound_without_parent_connections(
    runtime,
    supervisor,
    outcomes,
    expected,
):
    supervisor.run.side_effect = outcomes
    assert job_worker.main(["--drain", "--max-jobs", "2"]) == 0
    assert supervisor.run.call_count == expected
    supervisor.sleep.assert_not_called()
    runtime.database.open.assert_not_called()


@pytest.mark.parametrize("outcome", [0, job_worker.EXIT_IDLE])
def test_once_never_waits_or_starts_a_second_child(supervisor, outcome):
    supervisor.run.return_value = outcome
    assert job_worker.main(["--once"]) == 0
    supervisor.run.assert_called_once()
    supervisor.sleep.assert_not_called()


def test_persistent_worker_polls_and_finishes_current_child_on_sigterm(supervisor):
    completed = []

    def run(*_):
        if not completed:
            completed.append("idle")
            return job_worker.EXIT_IDLE
        supervisor.handlers[signal.SIGTERM]()
        completed.append("finished")
        return 0

    supervisor.run.side_effect = run
    assert job_worker.main([]) == 0
    assert completed == ["idle", "finished"]
    supervisor.sleep.assert_called_once_with(1)
    assert supervisor.run.call_count == 2


def test_idle_supervisor_checks_the_queue_instead_of_starting_children(supervisor):
    supervisor.answers.extend([False, False, True])

    def run(*_):
        supervisor.handlers[signal.SIGTERM]()
        return 0

    supervisor.run.side_effect = run
    assert job_worker.main(["--poll-interval", "7"]) == 0
    assert supervisor.run.call_count == 1
    assert [call.args[0] for call in supervisor.wait.call_args_list] == [7, 7]
    assert supervisor.watches[0].closed


@pytest.mark.parametrize("failure", [job_worker.EXIT_TIMEOUT, -signal.SIGKILL, 1])
def test_failed_attempt_releases_capacity_for_next_job_and_counts_toward_batch(
    supervisor,
    failure,
    capsys,
):
    supervisor.run.side_effect = [failure, 0]
    assert job_worker.main(["--drain", "--max-jobs", "2"]) == 1
    assert supervisor.run.call_count == 2
    assert "lease recovery remains enabled" in capsys.readouterr().err


def test_shutdown_during_hung_attempt_does_not_start_another(supervisor):
    def timeout(*_):
        supervisor.handlers[signal.SIGTERM]()
        return job_worker.EXIT_TIMEOUT

    supervisor.run.side_effect = timeout
    assert job_worker.main([]) == 1
    supervisor.run.assert_called_once()
    supervisor.sleep.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["--max-jobs", "0"],
        ["--max-jobs", "101"],
        ["--job-deadline", "0"],
        ["--job-deadline", "271"],
        ["--poll-interval", "0"],
    ],
)
def test_invalid_bounds_rejected_before_starting_child(supervisor, args):
    with pytest.raises(SystemExit):
        job_worker.main(args)
    supervisor.run.assert_not_called()


def test_configured_deadline_and_queue_file_reach_child(supervisor, tmp_path):
    queue_file = tmp_path / "queue.env"
    queue_file.touch()
    supervisor.run.return_value = 0
    assert (
        job_worker.main(
            [
                "--once",
                "--job-deadline",
                "200",
                "--queue-env-file",
                str(queue_file),
            ]
        )
        == 0
    )
    command, deadline = supervisor.run.call_args.args
    assert deadline == 200
    assert command[-2:] == ["--queue-env-file", str(queue_file)]


@pytest.mark.parametrize(
    "hang",
    [
        "while True: pass",
        # PyDLL deliberately holds the GIL: a timer thread or Python signal handler
        # in this child cannot enforce the deadline while this C call is running.
        "import ctypes; ctypes.PyDLL(None).sleep(60)",
        "try:\n    pass\nfinally:\n    while True: pass",
    ],
)
def test_real_supervisor_kills_and_reaps_unresponsive_child(tmp_path, hang):
    pid_file = tmp_path / "child.pid"
    script = (
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n" + hang
    )
    started = time.monotonic()
    assert job_worker.supervise([sys.executable, "-c", script, str(pid_file)], 2) == (
        job_worker.EXIT_TIMEOUT
    )
    assert time.monotonic() - started < 8
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_real_supervisor_preserves_completion_and_suppresses_child_content(capfd):
    assert (
        job_worker.supervise(
            [
                sys.executable,
                "-c",
                "print('PRIVATE-PROVIDER-TEXT'); raise SystemExit(10)",
            ],
            2,
        )
        == job_worker.EXIT_IDLE
    )
    assert "PRIVATE" not in str(capfd.readouterr())
