import httpx
import pytest
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from space.operational_errors import operation_unavailable


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PoolTimeout("SECRET"), "database connection busy"),
        (OperationalError("SECRET"), "database operation failed"),
        (httpx.ConnectError("SECRET"), "upstream connection failed"),
        (ValueError("SECRET"), "internal operation failed"),
    ],
)
def test_fixed_diagnostics_never_disclose_provider_content(error, reason):
    message = operation_unavailable(error)
    assert message == f"Operation unavailable ({reason}). Retry or check the Space website."
    assert "SECRET" not in message
