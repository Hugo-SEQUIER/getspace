"""Request-local, content-free timings shared with synchronous and object-reader threads."""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import perf_counter

STAGES = frozenset(
    {
        "db_pool",
        "db_transaction",
        "db_read",
        "db_recheck",
        "space_key",
        "object_queue",
        "object_get",
        "decode",
        "recall_total",
        "recall_sources",
        "recall_vector",
        "recall_lexical",
        "recall_context",
        "recall_assessment",
        "recall_auth",
        "embedding",
        "decision",
        "chat",
        "chat_assessment",
        "chat_conflicts",
    }
)
_current: ContextVar = ContextVar("read_timings", default=None)


class ReadTimings:
    def __init__(self):
        self.durations = Counter()
        self.lock = Lock()

    def add(self, stage, elapsed):
        if stage not in STAGES:
            raise ValueError("Unknown timing stage")
        with self.lock:
            self.durations[stage] += max(0, elapsed * 1000)

    def snapshot(self):
        """Parameters: none.

        What it does: copies the fixed, numeric stage durations under the lock.
        Output: a detached mapping of stage names to rounded milliseconds.
        """
        with self.lock:
            return {stage: round(duration, 1) for stage, duration in sorted(self.durations.items())}

    def header(self):
        with self.lock:
            return ", ".join(
                f"{stage};dur={duration:.1f}" for stage, duration in sorted(self.durations.items())
            )


@contextmanager
def collect_read_timings():
    timings = ReadTimings()
    token = _current.set(timings)
    try:
        yield timings
    finally:
        _current.reset(token)


def record_timing(stage, elapsed):
    if (timings := _current.get()) is not None:
        timings.add(stage, elapsed)


@contextmanager
def read_stage(stage):
    started = perf_counter()
    try:
        yield
    finally:
        record_timing(stage, perf_counter() - started)
