"""Parameters: synthetic records and opt-in disposable PostgreSQL fixtures.
What it does: verifies owner control, commit-only events and real REST/MCP mutation coverage.
Output: local evidence without hosted Space data, provider calls or live receivers.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_teams import create_team, join
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import webhook_events, webhook_retention, webhooks, workspace_items
from space.app import create_app
from space.security.auth import Unauthenticated
from space.settings import Settings
from space.workspace import CommandError

commands = commands_fixture
command_database = command_database_fixture
database = database_fixture
signing = signing_fixture


def database_data():
    return {
        "name": "Controlled development board",
        "view": "board",
        "groupBy": "stage",
        "fields": [
            {
                "id": "stage",
                "name": "Stage",
                "type": "select",
                "options": [
                    "To do",
                    "In progress",
                    "Review",
                    "Security Check",
                    "User Test",
                    "Done",
                ],
            },
            {"id": "blocker", "name": "Blocker", "type": "text"},
        ],
        "records": [
            {
                "id": "selected",
                "name": "PRIVATE TASK TITLE",
                "notes": "PRIVATE SPEC",
                "updatedAt": "2026-09-30T12:00:00Z",
                "values": {"stage": "To do", "blocker": ""},
            },
            {
                "id": "unrelated",
                "name": "UNRELATED PRIVATE TITLE",
                "notes": "UNRELATED PRIVATE NOTES",
                "updatedAt": "2026-09-30T12:00:00Z",
                "values": {"stage": "To do"},
            },
        ],
    }


def saved_database(service, owner, space):
    request = workspace_items.SaveItem(
        command_id=uuid4(),
        item_id=uuid4(),
        expected_revision=0,
        kind="database",
        data=database_data(),
    )
    workspace_items.save(service, owner, space, request)
    return request


def subscribe(service, owner, space, database_id, monkeypatch, *, transition=True):
    monkeypatch.setattr(webhooks, "resolve_endpoint", lambda _: ("8.8.8.8",))
    request = webhooks.CreateSubscription(
        command_id=uuid4(),
        database_id=database_id,
        endpoint_url="https://receiver.example.com/hooks/space",
        transition=webhooks.Transition(field_id="stage", to="Review") if transition else None,
    )
    return webhooks.create(service, owner, space, request), request


def next_save(request, *, stage=None, blocker=None, **extra):
    data = deepcopy(request.data)
    if stage is not None:
        data["records"][0]["values"]["stage"] = stage
    if blocker is not None:
        data["records"][0]["values"]["blocker"] = blocker
    data.update(extra)
    return workspace_items.SaveItem(
        command_id=uuid4(),
        item_id=request.item_id,
        expected_revision=request.expected_revision + 1,
        kind="database",
        data=data,
    )


def event_payloads(service, owner, space):
    with service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT * FROM space.webhook_events ORDER BY revision,record_id"
        ).fetchall()
        return [
            service._unpack(
                service._unwrap_key(
                    scope["organization_id"],
                    space,
                    service._key_row(connection, scope, row["key_id"]),
                ),
                scope,
                row["id"],
                1,
                "webhook_event",
                row["encrypted_payload"],
            )
            for row in rows
        ]


def test_row_diffs_ignore_order_noop_snapshot_and_timestamps():
    before = database_data()
    after = deepcopy(before)
    after["records"].reverse()
    for row in after["records"]:
        row["updatedAt"] = "2026-10-01T12:00:00Z"
    after["name"] = "Renamed database"
    assert list(webhook_events.record_changes(before, after)) == []
    after["records"][1]["values"]["stage"] = "Review"
    changes = list(webhook_events.record_changes(before, after))
    assert len(changes) == 1
    diff, _, _ = changes[0]
    assert diff == {
        "record_id": "selected",
        "operation": "updated",
        "changed_fields": ["stage"],
        "changed_properties": [],
    }


def test_projection_only_discloses_approved_select_transition_and_supports_reentry():
    fields = {field["id"]: field for field in database_data()["fields"]}
    before = {"values": {"stage": "In progress", "blocker": "PRIVATE"}}
    after = {"values": {"stage": "Review", "blocker": "PRIVATE EDIT"}}
    event = {"id": "stable", "transition": None}
    transition = {"field_id": "stage", "to": "Review"}
    result = webhook_events.projection(event, transition, before, after, fields)
    assert result["transition"] == {"field_id": "stage", "before": "In progress", "after": "Review"}
    assert "PRIVATE" not in str(result)
    assert webhook_events.projection(event, transition, after, after, fields) is None
    assert webhook_events.projection(event, transition, after, before, fields) is None
    assert webhook_events.projection(event, transition, before, None, fields) is None
    assert webhook_events.projection(event, None, before, after, fields) == event
    fields["stage"]["type"] = "text"
    assert webhook_events.projection(event, transition, before, after, fields) is None


def test_owner_crud_rotation_retries_and_no_secret_in_reads(commands, monkeypatch):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    created, create_request = subscribe(service, owner, space, board.item_id, monkeypatch)
    sub = created["subscription"]
    secret = created["signing_secret"]
    assert len(secret) >= 43
    assert webhooks.create(service, owner, space, create_request) == created
    assert secret not in str(webhooks.listing(service, owner, space))
    action = webhooks.ChangeSubscription(command_id=uuid4(), expected_revision=1, action="pause")
    paused = webhooks.change(service, owner, space, UUID(sub["id"]), action)
    assert paused["subscription"]["paused"] and "signing_secret" not in paused
    assert webhooks.change(service, owner, space, UUID(sub["id"]), action) == paused
    with pytest.raises(CommandError, match="changed"):
        webhooks.change(
            service,
            owner,
            space,
            UUID(sub["id"]),
            action.model_copy(update={"command_id": uuid4()}),
        )
    rotate = action.model_copy(
        update={"command_id": uuid4(), "expected_revision": 2, "action": "rotate"}
    )
    rotated = webhooks.change(service, owner, space, UUID(sub["id"]), rotate)
    assert rotated["signing_secret"] != secret
    assert secret not in str(webhooks.create(service, owner, space, create_request))
    removed = webhooks.change(
        service,
        owner,
        space,
        UUID(sub["id"]),
        rotate.model_copy(
            update={"command_id": uuid4(), "expected_revision": 3, "action": "remove"},
        ),
    )
    assert removed["subscription"]["removed_at"] and "signing_secret" not in removed
    assert webhooks.listing(service, owner, space)["items"] == []
    assert "signing_secret" not in webhooks.create(service, owner, space, create_request)


def test_cross_space_agent_and_nonowner_management_denied_without_dns_or_secret(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    created, request = subscribe(service, owner, space, board.item_id, monkeypatch)
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    agent = delegated(service, owner, write=True)
    monkeypatch.setattr(
        webhooks, "resolve_endpoint", lambda _: pytest.fail("Unauthorized DNS lookup")
    )
    for actor, sid in [(other, space), (owner, other_space), (agent, space)]:
        for operation in [
            lambda actor=actor, sid=sid: webhooks.listing(service, actor, sid),
            lambda actor=actor, sid=sid: webhooks.create(service, actor, sid, request),
            lambda actor=actor, sid=sid: webhooks.change(
                service,
                actor,
                sid,
                UUID(created["subscription"]["id"]),
                webhooks.ChangeSubscription(
                    command_id=uuid4(), expected_revision=1, action="pause"
                ),
            ),
        ]:
            with pytest.raises((CommandError, Unauthenticated)):
                operation()
    team = create_team(service, owner)
    team_space = UUID(team["id"])
    member, _ = join(service, owner, team)
    with pytest.raises(CommandError, match="owner"):
        webhooks.listing(service, member, team_space)
    with service.db.transaction(other) as connection:
        service._set_scope(connection, service.info(owner)["organization_id"], space)
        assert connection.execute("SELECT * FROM space.webhook_subscriptions").fetchall() == []
        assert (
            connection.execute(
                "SELECT * FROM space.active_webhook_subscriptions(%s)", (board.item_id,)
            ).fetchall()
            == []
        )


def test_subscriptions_require_active_same_space_database_and_real_select_options(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    _, request = subscribe(service, owner, space, board.item_id, monkeypatch)
    for changes in [
        {"database_id": uuid4()},
        {"transition": webhooks.Transition(field_id="blocker", to="Review")},
        {"transition": webhooks.Transition(field_id="stage", to="Missing")},
    ]:
        with pytest.raises(CommandError):
            webhooks.create(
                service, owner, space, request.model_copy(update={"command_id": uuid4(), **changes})
            )
    monkeypatch.setattr(
        webhooks, "resolve_endpoint", lambda _: (_ for _ in ()).throw(ValueError("PRIVATE DNS"))
    )
    with pytest.raises(CommandError, match="public addresses") as error:
        webhooks.create(service, owner, space, request.model_copy(update={"command_id": uuid4()}))
    assert "PRIVATE" not in error.value.message


def test_commit_only_events_snapshot_diffs_metadata_noops_conflicts_and_retry(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    subscription_id = UUID(created["subscription"]["id"])
    review = next_save(board, stage="Review")
    workspace_items.save(service, owner, space, review)
    workspace_items.save(service, owner, space, review)
    assert len(event_payloads(service, owner, space)) == 1
    metadata = next_save(review, blocker="PRIVATE BLOCKER")
    workspace_items.save(service, delegated(service, owner, write=True), space, metadata)
    assert len(webhooks.deliveries(service, owner, space, subscription_id)["items"]) == 1
    assert [event["record_id"] for event in event_payloads(service, owner, space)] == [
        "selected",
        "selected",
    ]
    noop = next_save(metadata)
    noop.data["records"].reverse()
    noop.data["records"][0]["updatedAt"] = "2026-10-01T12:00:00Z"
    workspace_items.save(service, owner, space, noop)
    assert len(event_payloads(service, owner, space)) == 2
    with pytest.raises(CommandError, match="changed"):
        workspace_items.save(
            service, owner, space, metadata.model_copy(update={"command_id": uuid4()})
        )
    original = webhook_events.enqueue

    def fail_after_outbox(*args):
        original(*args)
        raise RuntimeError("Controlled failed commit")

    monkeypatch.setattr(webhook_events, "enqueue", fail_after_outbox)
    failed = next_save(noop, records=deepcopy(metadata.data["records"]))
    failed.data["records"][0]["values"]["stage"] = "In progress"
    with pytest.raises(RuntimeError, match="Controlled"):
        workspace_items.save(service, owner, space, failed)
    assert workspace_items.get(service, owner, space, board.item_id)["revision"] == 4
    assert len(event_payloads(service, owner, space)) == 2
    assert "PRIVATE" not in str(event_payloads(service, owner, space))


def test_pause_remove_prevent_outbox_deliveries_and_resume_preserves_reentry(commands, monkeypatch):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    sid = UUID(created["subscription"]["id"])
    pause = webhooks.ChangeSubscription(command_id=uuid4(), expected_revision=1, action="pause")
    webhooks.change(service, owner, space, sid, pause)
    review = next_save(board, stage="Review")
    workspace_items.save(service, owner, space, review)
    assert event_payloads(service, owner, space) == []
    webhooks.change(
        service,
        owner,
        space,
        sid,
        pause.model_copy(
            update={
                "command_id": uuid4(),
                "expected_revision": 2,
                "action": "resume",
            }
        ),
    )
    left = next_save(review, stage="In progress")
    workspace_items.save(service, owner, space, left)
    resubmitted = next_save(left, stage="Review")
    workspace_items.save(service, owner, space, resubmitted)
    deliveries = webhooks.deliveries(service, owner, space, sid)["items"]
    assert len(deliveries) == 1 and deliveries[0]["revision"] == 4
    webhooks.change(
        service,
        owner,
        space,
        sid,
        pause.model_copy(
            update={
                "command_id": uuid4(),
                "expected_revision": 3,
                "action": "remove",
            }
        ),
    )
    workspace_items.save(service, owner, space, next_save(resubmitted, blocker="Edit"))
    assert len(event_payloads(service, owner, space)) == 2


def test_concurrent_snapshots_have_one_committed_event(commands, monkeypatch):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    subscribe(service, owner, space, board.item_id, monkeypatch)

    def save(_):
        try:
            return workspace_items.save(service, owner, space, next_save(board, stage="Review"))[
                "revision"
            ]
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [2, 409]
    assert len(event_payloads(service, owner, space)) == 1


def test_runtime_rows_are_readable_and_team_writer_has_only_filters(commands, monkeypatch):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    space = UUID(team["id"])
    member, _ = join(service, owner, team)
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    writer = replace(member, selected_space_id=space)
    workspace_items.save(service, writer, space, next_save(board, stage="Review"))
    with service.db.transaction(writer) as connection:
        service._scope(connection, writer, space)
        assert connection.execute("SELECT * FROM space.webhook_subscriptions").fetchall() == []
        filters = connection.execute(
            "SELECT * FROM space.active_webhook_subscriptions(%s)", (board.item_id,)
        ).fetchall()
        assert set(filters[0]) == {"id", "revision", "key_id", "encrypted_filter"}
        assert created["signing_secret"].encode() not in bytes(filters[0]["encrypted_filter"])
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        service.db.transaction(writer) as connection,
    ):
        service._scope(connection, writer, space)
        connection.execute("UPDATE space.webhook_attempts SET error_code='fake'")
    payloads = event_payloads(service, owner, space)
    assert len(payloads) == 1
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        encrypted = connection.execute(
            "SELECT encrypted_configuration FROM space.webhook_subscriptions"
        ).fetchone()
        # The operator owns this database: the signing secret is stored readably.
        assert created["signing_secret"].encode() in bytes(encrypted["encrypted_configuration"])


def test_redelivery_retains_event_identity_and_is_revision_safe_audited(commands, monkeypatch):
    service, owner, space, connect = commands
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    sid = UUID(created["subscription"]["id"])
    workspace_items.save(service, owner, space, next_save(board, stage="Review"))
    first = webhooks.deliveries(service, owner, space, sid)["items"][0]
    request = webhooks.Redeliver(command_id=uuid4())
    with pytest.raises(CommandError, match="completed"):
        webhooks.redeliver(service, owner, space, sid, UUID(first["id"]), request)
    with connect(admin=True) as connection:
        connection.execute(
            "UPDATE space.webhook_deliveries SET status='failed',error_code='timeout' WHERE id=%s",
            (first["id"],),
        )
    result = webhooks.redeliver(service, owner, space, sid, UUID(first["id"]), request)
    assert result["id"] == first["id"] and result["event_id"] == first["event_id"]
    assert result["cycle"] == 1 and result["status"] == "queued"
    assert webhooks.redeliver(service, owner, space, sid, UUID(first["id"]), request) == result
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute(
                "SELECT action FROM space.webhook_commands WHERE id=%s", (request.command_id,)
            ).fetchone()["action"]
            == "redeliver"
        )


def test_rest_and_official_mcp_writes_reach_same_outbox(commands, signing, monkeypatch):
    service, owner, space, _ = commands
    verifier, token = signing
    owner = replace(owner, issuer=verifier.issuer)
    space = UUID(service.bootstrap(owner)["id"])
    writer = delegated(service, owner, write=True)
    board = saved_database(service, owner, space)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    monkeypatch.setattr(webhooks, "resolve_endpoint", lambda _: ("8.8.8.8",))
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as client:
        client.headers["Authorization"] = "Bearer " + token(sub=owner.subject)
        response = client.post(
            f"/v1/spaces/{space}/webhooks",
            json=webhooks.CreateSubscription(
                command_id=uuid4(),
                database_id=board.item_id,
                endpoint_url="https://receiver.example.com/hook",
                transition=webhooks.Transition(field_id="stage", to="Review"),
            ).model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert webhook_events.QUEUED_HEADER not in response.headers
        review = next_save(board, stage="Review")
        saved = client.post(f"/v1/spaces/{space}/items", json=review.model_dump(mode="json"))
        assert saved.status_code == 200
        assert saved.headers[webhook_events.QUEUED_HEADER] == "1"
        client.headers["Authorization"] = "Bearer " + token(
            sub=owner.subject,
            aud=verifier.resource,
            client_id=writer.client_id,
            space_grant_id=writer.grant_id,
        )

        def save_through_mcp(change):
            response = client.post(
                "/mcp/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "save_workspace_item",
                        "arguments": {
                            "space_id": str(space),
                            "agent_id": writer.agent_id,
                            "change": change.model_dump(mode="json"),
                        },
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert not response.json()["result"].get("isError"), response.text
            return response.headers.get(webhook_events.QUEUED_HEADER)

        # Leaving Review records an event, but the Review filter queues no delivery.
        left = next_save(review, stage="In progress")
        assert save_through_mcp(left) is None
        resubmit = next_save(left, stage="Review")
        assert save_through_mcp(resubmit) == "1"
    assert [e["revision"] for e in event_payloads(service, owner, space)] == [2, 3, 4]
    assert (
        len(
            webhooks.deliveries(service, owner, space, UUID(response.json()["subscription"]["id"]))[
                "items"
            ]
        )
        == 2
    )


def test_retention_and_content_reset_remove_webhook_state_without_general_column_changes(
    commands, monkeypatch
):
    from space import tasks
    from space.purge import purge_space

    service, owner, space, connect = commands
    before = tasks.columns(service, owner, space)
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    workspace_items.save(service, owner, space, next_save(board, stage="Review"))
    assert tasks.columns(service, owner, space) == before
    with connect(admin=True) as connection:
        connection.execute(
            "UPDATE space.webhook_deliveries SET status='delivered',"
            "created_at=now()-interval '31 days' WHERE space_id=%s",
            (space,),
        )
        connection.execute(
            "UPDATE space.webhook_events SET created_at=now()-interval '31 days' WHERE space_id=%s",
            (space,),
        )
    with connect() as connection:
        result = webhook_retention.prune(connection)
    assert result["deliveries"] == 1 and result["events"] == 1
    assert (
        webhooks.listing(service, owner, space)["items"][0]["id"] == created["subscription"]["id"]
    )
    scope, _ = service._key(owner, space)
    with connect() as connection:
        result = purge_space(connection, scope["organization_id"], space, objects=service.objects)
    assert result["webhook_subscriptions"] == 1
    assert webhooks.listing(service, owner, space)["items"] == []


def test_generic_subscriptions_emit_creates_deletes_only_for_selected_database(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch, transition=False)
    other_board = saved_database(service, owner, space)
    workspace_items.save(service, owner, space, next_save(other_board, stage="Review"))
    assert event_payloads(service, owner, space) == []
    changed = next_save(board)
    changed.data["records"] = [
        changed.data["records"][1],
        {
            "id": "new-row",
            "name": "PRIVATE NEW ROW",
            "notes": "PRIVATE NEW NOTES",
            "updatedAt": "2026-10-01T12:00:00Z",
            "values": {"stage": "Review"},
        },
    ]
    workspace_items.save(service, owner, space, changed)
    events = event_payloads(service, owner, space)
    assert {(event["record_id"], event["operation"]) for event in events} == {
        ("selected", "deleted"),
        ("new-row", "created"),
    }
    assert all(event["transition"] is None for event in events)
    assert "PRIVATE" not in str(events)
    assert (
        len(
            webhooks.deliveries(service, owner, space, UUID(created["subscription"]["id"]))["items"]
        )
        == 2
    )
    workspace_items.save(service, owner, space, next_save(changed, name="Renamed database"))
    assert len(event_payloads(service, owner, space)) == 2


def test_archive_replace_emits_only_final_changed_row_with_revision_hop(commands, monkeypatch):
    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge

    service, owner, space, _ = commands
    board = saved_database(service, owner, space)
    incoming_review = next_save(board, stage="Review")
    workspace_items.save(service, owner, space, incoming_review)
    archive = read_archive(service, owner, space)
    current = next_save(incoming_review, stage="In progress")
    workspace_items.save(service, owner, space, current)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    upload = stage(service, owner, space, archive)
    choices = {f"item:{board.item_id}": "replace"}
    reviewed = restore_merge.review(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Review(choices=choices),
    )
    request = restore_merge.Commit(snapshot=reviewed["snapshot"], choices=choices)
    assert restore_merge.commit(service, owner, space, upload.id, request)["state"] == "committed"
    restored = workspace_items.get(service, owner, space, board.item_id)
    assert restored["records"][0]["values"]["stage"] == "Review"
    events = event_payloads(service, owner, space)
    assert len(events) == 1 and events[0]["record_id"] == "selected"
    assert events[0]["previous_revision"] == 3 and events[0]["revision"] == restored["revision"]
    assert restored["revision"] > 4
    deliveries = webhooks.deliveries(service, owner, space, UUID(created["subscription"]["id"]))[
        "items"
    ]
    assert len(deliveries) == 1
    assert restore_merge.commit(service, owner, space, upload.id, request)["state"] == "committed"
    assert len(event_payloads(service, owner, space)) == 1
