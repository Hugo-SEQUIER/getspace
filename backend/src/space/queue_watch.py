"""Cheap idle checks for long-running worker supervisors.

A supervisor runs each attempt in a fresh child process, which costs a Python start-up.
While a queue is empty it instead asks one read-only question over a single restricted
connection, and starts a child only when eligible work exists.
"""

import logging
import time
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class QueueWatch:
    def __init__(self, url: str, options: dict, restrict: Callable, sql: str):
        self.url, self.options, self.restrict, self.sql = url, options, restrict, sql
        self.connection = None
        self.failing = False

    def pending(self) -> bool:
        """True when the queue holds eligible work. A database failure reads as idle, so
        the supervisor waits and retries instead of starting attempts that cannot run."""
        try:
            if self.connection is None or self.connection.closed:
                self.connection = psycopg.connect(
                    self.url, **self.options, connect_timeout=5, row_factory=dict_row
                )
                self.restrict(self.connection)
            pending = bool(self.connection.execute(self.sql).fetchone()["pending"])
            # End the read so the login's idle-in-transaction timeout never applies.
            self.connection.rollback()
            if self.failing:
                logger.warning("Queue check recovered")
                self.failing = False
            return pending
        except Exception:
            if not self.failing:
                logger.warning("Queue check failed; retrying while idle")
                self.failing = True
            self.close()
            return False

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                logger.debug("Queue check connection did not close cleanly")
            self.connection = None


def wait(seconds: float, stopping: Callable[[], bool]) -> None:
    """Sleep up to the given seconds, waking early once a shutdown is requested."""
    deadline = time.monotonic() + seconds
    while not stopping():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))
