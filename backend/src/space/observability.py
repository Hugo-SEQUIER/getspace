"""Bounded request metrics with no content, credentials, queries or account identifiers."""

import threading
import time
from collections import Counter


class RequestMetrics:
    def __init__(self):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.counts = Counter()
        self.latency = Counter()
        self.total_ms = 0.0
        self.maximum_ms = 0.0

    def record(self, status, elapsed):
        milliseconds = max(0, elapsed * 1000)
        bucket = next(
            (
                str(limit)
                for limit in (100, 500, 1000, 5000, 30000, 120000)
                if milliseconds <= limit
            ),
            "+Inf",
        )
        with self.lock:
            self.counts[f"{min(5, max(1, status // 100))}xx"] += 1
            self.latency[bucket] += 1
            self.total_ms += milliseconds
            self.maximum_ms = max(self.maximum_ms, milliseconds)

    def snapshot(self):
        with self.lock:
            return {
                "scope": "process",
                "uptime_seconds": round(time.monotonic() - self.started),
                "responses": dict(self.counts),
                "latency_ms_buckets": dict(self.latency),
                "latency_ms_sum": round(self.total_ms),
                "latency_ms_max": round(self.maximum_ms),
            }


def database_pool_stats(workspace):
    """Operator-only pool counters; never connection strings or query text."""
    pool = getattr(getattr(workspace, "db", None), "pool", None)
    if pool is None:
        return {}
    allowed = {
        "pool_min",
        "pool_max",
        "pool_size",
        "pool_available",
        "requests_waiting",
        "requests_num",
        "requests_queued",
        "requests_wait_ms",
        "requests_errors",
        "usage_ms",
        "connections_num",
        "connections_ms",
        "connections_errors",
        "connections_lost",
        "returns_bad",
    }
    return {
        key: value
        for key, value in pool.get_stats().items()
        if key in allowed and type(value) is int and value >= 0
    }


def database_health(workspace):
    with workspace.db.pool.connection() as connection:
        workspace.db.check_environment(connection)
        return connection.execute("SELECT space.operational_health() AS health").fetchone()[
            "health"
        ]
