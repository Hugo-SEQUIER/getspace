import pytest

from space.settings import Settings
from space.workspace import Database


def test_connection_budget_counts_api_workers_jobs_replicas_and_rollout_overlap():
    settings = Settings(
        environment="test",
        database_pool_size=4,
        database_api_processes=2,
        database_worker_processes=2,
        database_replicas=2,
        database_deployment_overlap=2,
    )
    assert settings.database_connection_budget == 40
    with pytest.raises(RuntimeError, match="requires 40"):
        Database.validate_budget(5, settings.database_connection_budget)
    Database.validate_budget(40, settings.database_connection_budget)
    Database.validate_budget(-1, settings.database_connection_budget)


def test_default_budget_fits_the_existing_single_process_role_allowance(monkeypatch):
    monkeypatch.delenv("BACKEND_WORKERS", raising=False)
    settings = Settings(environment="test")
    assert settings.database_connection_budget == 5


def test_shared_api_capacity_retains_rollout_and_worker_headroom():
    settings = Settings(
        environment="test",
        database_pool_size=8,
        database_api_processes=1,
        database_worker_processes=1,
        database_replicas=1,
        database_deployment_overlap=2,
    )
    assert settings.database_connection_budget == 18
    Database.validate_budget(18, settings.database_connection_budget)
    with pytest.raises(RuntimeError):
        Database.validate_budget(6, settings.database_connection_budget)


def test_webhook_delivery_process_adds_its_application_connection_per_rollout_version():
    settings = Settings(
        environment="test",
        database_pool_size=8,
        database_api_processes=1,
        database_worker_processes=1,
        database_webhook_processes=1,
        database_replicas=1,
        database_deployment_overlap=2,
    )
    assert settings.database_connection_budget == 20
    with pytest.raises(RuntimeError, match="requires 20"):
        Database.validate_budget(18, settings.database_connection_budget)
    Database.validate_budget(20, settings.database_connection_budget)


@pytest.mark.parametrize("pool,other", [(6, 2), (2, 6)])
def test_regional_api_pools_share_the_existing_eighteen_connection_allowance(pool, other):
    settings = Settings(
        environment="test",
        database_pool_size=pool,
        database_additional_connections=other,
        database_api_processes=1,
        database_worker_processes=1,
        database_replicas=1,
        database_deployment_overlap=2,
    )
    assert settings.database_connection_budget == 18
    Database.validate_budget(18, settings.database_connection_budget)


def test_operator_pool_stats_only_include_known_nonnegative_integer_counters():
    from types import SimpleNamespace

    from space.observability import database_pool_stats

    pool = SimpleNamespace(
        get_stats=lambda: {
            "pool_max": 8,
            "pool_available": 5,
            "requests_wait_ms": 3100,
            "connection_string": "secret",
            "pool_min": "secret",
            "connections_errors": -1,
        }
    )
    assert database_pool_stats(SimpleNamespace(db=SimpleNamespace(pool=pool))) == {
        "pool_max": 8,
        "pool_available": 5,
        "requests_wait_ms": 3100,
    }
    assert database_pool_stats(None) == {}
