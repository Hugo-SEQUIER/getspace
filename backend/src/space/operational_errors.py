"""Fixed, content-free diagnostics for otherwise opaque transport failures."""

import httpx
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from space.model_provider import ModelError


def operation_unavailable(error):
    if isinstance(error, PoolTimeout):
        reason = "database connection busy"
    elif isinstance(error, DatabaseError):
        reason = "database operation failed"
    elif isinstance(error, httpx.RequestError):
        reason = "upstream connection failed"
    elif isinstance(error, ModelError) and error.status is not None:
        # The status is a number; the provider's answer was never kept.
        reason = f"model provider answered HTTP {error.status}"
    else:
        reason = "internal operation failed"
    return f"Operation unavailable ({reason}). Retry or check the Space website."
