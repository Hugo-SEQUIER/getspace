"""Parameters: synthetic databases, inbox subscriptions and opt-in disposable PostgreSQL fixtures.
What it does: verifies that the owner's agents lease, acknowledge and recover inbox deliveries.
Output: local evidence without hosted Space data, receivers or model-provider calls.
"""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_teams import create_team, join
from test_webhook_delivery import queue as queue_fixture
from test_webhooks import next_save, saved_database
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import webhook_delivery, webhook_events, webhook_inbox, webhooks, workspace_items
from space.app import create_app
from space.settings import Settings
from space.workspace import CommandError

commands = commands_fixture
command_database = command_database_fixture
database = database_fixture
signing = signing_fixture
queue = queue_fixture


def inbox(service, owner, space, database_id, *, transition=True):
    request = webhooks.CreateSubscription(
        command_id=uuid4(),
        database_id=database_id,
        delivery="inbox",
        transition=webhooks.Transition(field_id="stage", to="Review") if transition else None,
    )
    return webhooks.create(service, owner, space, request), request


def pull(service, agent, space, **fields):
    return webhook_inbox.pull(service, agent, space, webhook_inbox.PullEvents(**fields))


def ack(service, agent, space, *ids):
    return webhook_inbox.acknowledge(
        service, agent, space, webhook_inbox.AcknowledgeEvents(delivery_ids=list(ids))
    )


def test_create_request_requires_a_destination_that_matches_the_delivery_kind():
    with pytest.raises(ValueError, match="receiver or inbox"):
        webhooks.CreateSubscription(command_id=uuid4(), database_id=uuid4())
    with pytest.raises(ValueError, match="no receiver"):
        webhooks.CreateSubscription(
            command_id=uuid4(),
            database_id=uuid4(),
            delivery="inbox",
            endpoint_url="https://receiver.example.com/hook",
        )
    assert webhooks.CreateSubscription(command_id=uuid4(), database_id=uuid4(), delivery="inbox")
    with pytest.raises(ValueError):
        webhook_inbox.PullEvents(limit=0)
    with pytest.raises(ValueError):
        webhook_inbox.AcknowledgeEvents(delivery_ids=[])


def test_inbox_subscription_has_no_secret_endpoint_or_dns_lookup_and_cannot_rotate(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    monkeypatch.setattr(
        webhooks, "resolve_endpoint", lambda _: pytest.fail("Inbox delivery resolved DNS")
    )
    board = saved_database(service, owner, space)
    created, request = inbox(service, owner, space, board.item_id)
    sub = created["subscription"]
    assert "signing_secret" not in created
    assert sub["delivery"] == "inbox" and sub["endpoint_url"] is None
    assert webhooks.create(service, owner, space, request) == created
    listed = webhooks.listing(service, owner, space)["items"]
    assert listed[0]["delivery"] == "inbox" and "signing_secret" not in str(listed)
    rotate = webhooks.ChangeSubscription(command_id=uuid4(), expected_revision=1, action="rotate")
    with pytest.raises(CommandError, match="no signing secret"):
        webhooks.change(service, owner, space, UUID(sub["id"]), rotate)
    paused = webhooks.change(
        service,
        owner,
        space,
        UUID(sub["id"]),
        rotate.model_copy(update={"action": "pause"}),
    )
    assert paused["subscription"]["paused"] and "signing_secret" not in paused


def test_owner_agent_pulls_leases_acknowledges_and_recovers_expired_leases(commands, queue):
    service, owner, space, connect = commands
    board = saved_database(service, owner, space)
    created, _ = inbox(service, owner, space, board.item_id)
    sid = UUID(created["subscription"]["id"])
    agent = delegated(service, owner, write=True)
    assert pull(service, agent, space) == {"items": [], "has_more": False, "lease_seconds": 600}
    with webhook_events.watch_queued_deliveries() as marks:
        workspace_items.save(service, owner, space, next_save(board, stage="Review"))
    # Inbox deliveries never wake the HTTPS worker, and the worker never claims them.
    assert marks == []
    assert webhook_delivery.claim(queue) is None
    first = pull(service, agent, space, limit=1)
    assert len(first["items"]) == 1 and not first["has_more"]
    item = first["items"][0]
    event = item["event"]
    assert item["subscription_id"] == str(sid) and item["attempt"] == 1 and item["cycle"] == 0
    assert item["database_id"] == str(board.item_id)
    assert event["type"] == "database.record.changed" and event["record_id"] == "selected"
    assert event["transition"] == {"field_id": "stage", "before": "To do", "after": "Review"}
    assert "PRIVATE" not in str(first)
    # A leased delivery is invisible to a second puller until the lease expires.
    other = delegated(service, owner, write=True)
    assert pull(service, other, space)["items"] == []
    with connect(admin=True) as connection:
        connection.execute(
            "UPDATE space.webhook_deliveries SET lease_until=clock_timestamp()-interval '1 second'"
        )
    second = pull(service, other, space, subscription_id=sid)
    assert [i["delivery_id"] for i in second["items"]] == [item["delivery_id"]]
    assert second["items"][0]["attempt"] == 2
    receipts = webhooks.deliveries(service, owner, space, sid)["items"][0]
    assert receipts["status"] == "processing" and receipts["attempts"] == 2
    assert [(r["attempt"], r["error_code"]) for r in receipts["receipts"]] == [
        (1, "worker_lost"),
        (2, None),
    ]
    assert receipts["receipts"][1]["finished_at"] is None
    result = ack(service, other, space, UUID(item["delivery_id"]), uuid4())
    assert [i["status"] for i in result["items"]] == ["acknowledged", "not_found"]
    done = webhooks.deliveries(service, owner, space, sid)["items"][0]
    assert done["status"] == "delivered" and done["error_code"] is None
    assert done["receipts"][1]["finished_at"] is not None
    assert ack(service, agent, space, UUID(item["delivery_id"]))["items"][0]["status"] == (
        "already_done"
    )
    assert pull(service, agent, space)["items"] == []
    assert "PRIVATE" not in str(webhooks.deliveries(service, owner, space, sid))


def test_filters_pause_exhaustion_and_redelivery(commands, queue):
    service, owner, space, connect = commands
    board = saved_database(service, owner, space)
    created, _ = inbox(service, owner, space, board.item_id, transition=False)
    sid = UUID(created["subscription"]["id"])
    agent = delegated(service, owner, write=True)
    workspace_items.save(service, owner, space, next_save(board, blocker="PRIVATE BLOCKER"))
    assert pull(service, agent, space, database_id=uuid4())["items"] == []
    assert pull(service, agent, space, subscription_id=uuid4())["items"] == []
    pulled = pull(service, agent, space, database_id=board.item_id)
    assert len(pulled["items"]) == 1 and "PRIVATE" not in str(pulled)
    assert pulled["items"][0]["event"]["changed_fields"] == ["blocker"]
    delivery_id = UUID(pulled["items"][0]["delivery_id"])
    pause = webhooks.ChangeSubscription(command_id=uuid4(), expected_revision=1, action="pause")
    webhooks.change(service, owner, space, sid, pause)
    with connect(admin=True) as connection:
        connection.execute(
            "UPDATE space.webhook_deliveries SET lease_until=clock_timestamp()-interval '1 second'"
        )
    assert pull(service, agent, space)["items"] == []
    webhooks.change(
        service,
        owner,
        space,
        sid,
        pause.model_copy(
            update={"command_id": uuid4(), "expected_revision": 2, "action": "resume"}
        ),
    )
    for attempt in range(2, 6):
        assert pull(service, agent, space)["items"][0]["attempt"] == attempt
        with connect(admin=True) as connection:
            connection.execute(
                "UPDATE space.webhook_deliveries "
                "SET lease_until=clock_timestamp()-interval '1 second'"
            )
    # Five pulls without acknowledgement exhaust the cycle; the worker's maintenance
    # pass makes that visible, and the owner can redeliver from the receipts.
    assert pull(service, agent, space)["items"] == []
    assert webhook_delivery.claim(queue) is None
    failed = webhooks.deliveries(service, owner, space, sid)["items"][0]
    assert failed["status"] == "failed" and failed["error_code"] == "attempts_exhausted"
    assert ack(service, agent, space, delivery_id)["items"][0]["status"] == "already_done"
    redelivered = webhooks.redeliver(
        service, owner, space, sid, delivery_id, webhooks.Redeliver(command_id=uuid4())
    )
    assert redelivered["cycle"] == 1 and redelivered["status"] == "queued"
    again = pull(service, agent, space)["items"][0]
    assert again["delivery_id"] == str(delivery_id) and again["cycle"] == 1
    assert again["attempt"] == 1
    assert ack(service, agent, space, delivery_id)["items"][0]["status"] == "acknowledged"


def test_inbox_requires_the_owner_write_grant_and_hides_other_spaces(commands):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    inbox(service, owner, space, board.item_id)
    workspace_items.save(service, owner, space, next_save(board, stage="Review"))
    reader = delegated(service, owner)
    with pytest.raises(CommandError) as error:
        pull(service, reader, space)
    assert error.value.status == 403
    with pytest.raises(CommandError):
        ack(service, reader, space, uuid4())
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    stranger = delegated(service, other, write=True)
    with pytest.raises(CommandError):
        pull(service, stranger, space)
    assert pull(service, stranger, other_space)["items"] == []
    team = create_team(service, owner)
    team_space = UUID(team["id"])
    member, _ = join(service, owner, team)
    team_board = saved_database(service, owner, team_space)
    inbox(service, owner, team_space, team_board.item_id)
    writer = replace(member, selected_space_id=team_space)
    workspace_items.save(service, writer, team_space, next_save(team_board, stage="Review"))
    member_agent = delegated(service, replace(member, selected_space_id=team_space), write=True)
    with pytest.raises(CommandError, match="owner") as error:
        pull(service, member_agent, team_space)
    assert error.value.status == 403
    owner_agent = delegated(service, replace(owner, selected_space_id=team_space), write=True)
    assert len(pull(service, owner_agent, team_space)["items"]) == 1


def test_official_mcp_tools_pull_and_acknowledge_without_waking_the_worker(commands, signing):
    service, owner, space, _ = commands
    verifier, token = signing
    owner = replace(owner, issuer=verifier.issuer)
    space = UUID(service.bootstrap(owner)["id"])
    writer = delegated(service, owner, write=True)
    board = saved_database(service, owner, space)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as client:
        client.headers["Authorization"] = "Bearer " + token(sub=owner.subject)
        response = client.post(
            f"/v1/spaces/{space}/webhooks",
            json={
                "command_id": str(uuid4()),
                "database_id": str(board.item_id),
                "delivery": "inbox",
                "transition": {"field_id": "stage", "to": "Review"},
            },
        )
        assert response.status_code == 200, response.text
        assert "signing_secret" not in response.json()
        assert response.json()["subscription"]["delivery"] == "inbox"
        saved = client.post(
            f"/v1/spaces/{space}/items",
            json=next_save(board, stage="Review").model_dump(mode="json"),
        )
        assert saved.status_code == 200
        assert webhook_events.QUEUED_HEADER not in saved.headers
        client.headers["Authorization"] = "Bearer " + token(
            sub=owner.subject,
            aud=verifier.resource,
            client_id=writer.client_id,
            space_grant_id=writer.grant_id,
        )

        def call(name, **arguments):
            response = client.post(
                "/mcp/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": name,
                        "arguments": {
                            "space_id": str(space),
                            "agent_id": writer.agent_id,
                            **arguments,
                        },
                    },
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert not result.get("isError"), response.text
            return result["structuredContent"]

        pulled = call("pull_events")
        assert len(pulled["items"]) == 1 and pulled["has_more"] is False
        event = pulled["items"][0]["event"]
        assert event["transition"]["after"] == "Review" and "PRIVATE" not in str(pulled)
        acknowledged = call(
            "ack_events", request={"delivery_ids": [pulled["items"][0]["delivery_id"]]}
        )
        assert acknowledged["items"][0]["status"] == "acknowledged"
        assert call("pull_events", request={"limit": 5})["items"] == []
