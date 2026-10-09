from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import quotas
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_retained_history_is_charged_and_retries_do_not_charge_twice(commands):
    service, owner, space, _ = commands
    request = change()
    saved = service.save(owner, space, request)
    first = quotas.usage(service, owner, space)
    assert first["items"] == 1 and first["retained_bytes"] > 0
    service.save(owner, space, request)
    assert quotas.usage(service, owner, space)["retained_bytes"] == first["retained_bytes"]
    service.save(
        owner,
        space,
        change().model_copy(update={"resource_id": UUID(saved["id"]), "expected_revision": 1}),
    )
    latest = quotas.usage(service, owner, space)
    assert latest["items"] == 1 and latest["retained_bytes"] > first["retained_bytes"]


def test_item_and_byte_limits_abort_the_entire_write(commands):
    service, owner, space, connect = commands
    service.save(owner, space, change())
    with connect() as connection:
        connection.execute("UPDATE space.usage_limits SET item_limit=1 WHERE space_id=%s", (space,))
    with pytest.raises(CommandError, match="item limit") as error:
        service.save(owner, space, change())
    assert error.value.status == 429
    assert quotas.usage(service, owner, space)["items"] == 1
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET item_limit=10,storage_limit_bytes=retained_bytes "
            "WHERE space_id=%s",
            (space,),
        )
    with pytest.raises(CommandError, match="storage limit"):
        service.save(owner, space, change())
    assert quotas.usage(service, owner, space)["items"] == 1


def test_monthly_model_budget_is_atomic_across_concurrent_requests(commands):
    service, owner, space, connect = commands
    quotas.consume_model_request(service, owner, space)
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET model_request_limit=3 WHERE space_id=%s", (space,)
        )

    def consume(_):
        try:
            quotas.consume_model_request(service, owner, space)
            return 200
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(consume, range(8))) == [200, 200] + [429] * 6
    assert quotas.usage(service, owner, space)["model_requests"] == 3
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET month='2020-01-01' WHERE space_id=%s", (space,)
        )
    quotas.consume_model_request(service, owner, space)
    assert quotas.usage(service, owner, space)["model_requests"] == 1
