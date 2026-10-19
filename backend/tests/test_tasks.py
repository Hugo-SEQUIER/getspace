"""Parameters: isolated PostgreSQL fixtures and public task commands.
What it does: checks encrypted columns, atomic moves, authorization and transport parity.
Output: regression evidence without reading local or hosted credentials.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import tasks, workspace_items
from space.app import create_app
from space.memory import RecallMetadata
from space.settings import Settings
from space.workspace import CommandError, SaveResource

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def task(service, owner, space, **values):
    return service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            expected_revision=0,
            kind="task",
            title="Launch checklist",
            body="Keep the exact task description and café notes.",
            **values,
        ),
    )


def column(**values):
    return tasks.AddColumn(
        command_id=uuid4(), expected_revision=1, value="backlog", label="Backlog", **values
    )


def move(**values):
    return tasks.SetStatus(command_id=uuid4(), expected_revision=1, status="done", **values)


def test_custom_status_input_is_bounded_and_requires_visible_labels():
    assert column().label == "Backlog"
    assert column().model_copy(update={"label": "À venir"}).label == "À venir"
    for values in ({"status": ""}, {"status": "bad value"}, {"status": "a" * 81}):
        with pytest.raises(ValidationError):
            tasks.SetStatus(command_id=uuid4(), expected_revision=1, **values)
    for label in ("  ", "bad\nlabel", "a" * 81):
        with pytest.raises(ValidationError):
            tasks.AddColumn(command_id=uuid4(), expected_revision=1, value="backlog", label=label)
    with pytest.raises(ValidationError):
        tasks.SetStatus(command_id=uuid4(), expected_revision=True, status="done")


def test_columns_extend_the_canonical_collection_and_retry_exactly(commands):
    service, owner, space, _ = commands
    original = tasks.columns(service, owner, space)
    assert original == {
        "revision": 1,
        "columns": [
            {"value": "todo", "label": "To do"},
            {"value": "in_progress", "label": "In progress"},
            {"value": "done", "label": "Done"},
        ],
    }
    request = column()
    saved = tasks.add_column(service, owner, space, request)
    assert saved["columns"] == original["columns"] + [{"value": "backlog", "label": "Backlog"}]
    assert saved["revision"] == 2
    later = tasks.add_column(
        service,
        owner,
        space,
        tasks.AddColumn(
            command_id=uuid4(),
            expected_revision=2,
            value="waiting",
            label="À valider",
        ),
    )
    assert later["revision"] == 3
    assert tasks.add_column(service, owner, space, request) == saved
    assert tasks.columns(service, owner, space) == later
    with service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
        rows = connection.execute("SELECT * FROM space.collections").fetchall()
        assert len(rows) == 1
        assert b"Backlog" in bytes(rows[0]["encrypted_schema"])  # readable JSON
        key_row = service._key_row(connection, scope, rows[0]["key_id"])
    key = service._unwrap_key(scope["organization_id"], space, key_row)
    schema = service._unpack(key, scope, rows[0]["id"], 1, "schema", rows[0]["encrypted_schema"])
    assert schema["fields"]["status"] == ["todo", "in_progress", "done", "backlog", "waiting"]
    assert schema["fields"]["title"] == "text"


def test_column_conflicts_and_invalid_status_never_change_tasks(commands):
    service, owner, space, _ = commands
    request = column()
    tasks.add_column(service, owner, space, request)
    for change in (
        request.model_copy(update={"command_id": uuid4()}),
        request.model_copy(update={"label": "Changed"}),
        request.model_copy(update={"command_id": uuid4(), "expected_revision": 2}),
        request.model_copy(
            update={
                "command_id": uuid4(),
                "expected_revision": 2,
                "value": "other",
                "label": "bAcKlOg",
            }
        ),
    ):
        with pytest.raises(CommandError) as error:
            tasks.add_column(service, owner, space, change)
        assert error.value.status == 409
    saved = task(service, owner, space, status="backlog")
    assert service.get_resource(owner, space, UUID(saved["id"])) == saved


def test_a_new_status_becomes_a_column_and_needs_you_stays_built_in(commands):
    service, owner, space, _ = commands
    saved = task(service, owner, space)
    moved = tasks.set_status(
        service, owner, space, UUID(saved["id"]), move().model_copy(update={"status": "in_review"})
    )
    assert moved["status"] == "in_review"
    created = task(service, owner, space, status="blocked")
    assert created["status"] == "blocked"
    task(service, owner, space, status="needs_you")
    config = tasks.columns(service, owner, space)
    assert config["revision"] == 3
    assert config["columns"][-2:] == [
        {"value": "in_review", "label": "In review"},
        {"value": "blocked", "label": "Blocked"},
    ]
    assert "needs_you" not in {column["value"] for column in config["columns"]}
    # Reusing a status adds nothing.
    task(service, owner, space, status="in_review")
    assert tasks.columns(service, owner, space) == config


def test_a_new_status_named_like_an_existing_column_is_refused(commands):
    service, owner, space, _ = commands
    tasks.add_column(
        service,
        owner,
        space,
        column().model_copy(update={"value": "review_queue", "label": "Review"}),
    )
    with pytest.raises(CommandError, match="review_queue") as error:
        task(service, owner, space, status="review")
    assert error.value.status == 409
    assert [column["value"] for column in tasks.columns(service, owner, space)["columns"]] == [
        "todo",
        "in_progress",
        "done",
        "review_queue",
    ]


def test_status_move_preserves_content_history_and_index_without_resource_roundtrips(
    commands,
    monkeypatch,
):
    service, owner, space, _ = commands
    project = workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="project",
            data={"name": "Launch"},
        ),
    )
    saved = task(
        service,
        owner,
        space,
        project=UUID(project["id"]),
        priority="High",
        recall=RecallMetadata(topics=["Launch", "release"], excluded=True),
    )
    rid = UUID(saved["id"])
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        before = connection.execute(
            "SELECT field,terms,occurrences,tokens FROM space.resource_terms "
            "WHERE resource_id=%s ORDER BY field",
            (rid,),
        ).fetchall()
    transactions, unwraps = [], []
    transaction, unwrap = service.db.transaction, service.keys.unwrap

    @contextmanager
    def counted_transaction(*args):
        transactions.append(1)
        with transaction(*args) as connection:
            yield connection

    def counted_key(*args):
        unwraps.append(1)
        return unwrap(*args)

    with monkeypatch.context() as patch:
        patch.setattr(service.db, "transaction", counted_transaction)
        patch.setattr(service.keys, "unwrap", counted_key)
        patch.setattr(service, "get_resource", lambda *args: pytest.fail("Full resource reread"))
        patch.setattr(service, "save", lambda *args: pytest.fail("Generic full save"))
        patch.setattr(service.objects, "get", lambda *args: pytest.fail("Object read"))
        patch.setattr(service.objects, "put", lambda *args: pytest.fail("Object write"))
        patch.setattr(
            "space.workspace.resource_fingerprints",
            lambda *args: pytest.fail("Unnecessary token generation"),
        )
        updated = tasks.set_status(service, owner, space, rid, move())
    assert len(transactions) == 3
    assert len(unwraps) == 1
    assert updated["revision"] == 2 and updated["status"] == "done"
    for field in ("id", "title", "body", "priority", "project", "recall"):
        assert updated[field] == saved[field]
    assert service.get_resource(owner, space, rid, 1) == saved
    assert service.get_resource(owner, space, rid) == updated
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        after = connection.execute(
            "SELECT field,terms,occurrences,tokens FROM space.resource_terms "
            "WHERE resource_id=%s ORDER BY field",
            (rid,),
        ).fetchall()
        assert before == after
        revisions = connection.execute(
            "SELECT DISTINCT revision FROM space.resource_terms WHERE resource_id=%s",
            (rid,),
        ).fetchall()
        assert revisions == [{"revision": 2}]
        assert connection.execute("SELECT count(*) AS n FROM space.events").fetchone()["n"] == 2


def test_status_retries_return_original_revision_after_later_changes(commands):
    service, owner, space, _ = commands
    saved = task(service, owner, space)
    rid = UUID(saved["id"])
    request = move()
    first = tasks.set_status(service, owner, space, rid, request)
    later = tasks.set_status(
        service,
        owner,
        space,
        rid,
        tasks.SetStatus(
            command_id=uuid4(),
            expected_revision=2,
            status="in_progress",
        ),
    )
    assert tasks.set_status(service, owner, space, rid, request) == first
    assert service.get_resource(owner, space, rid) == later
    with pytest.raises(CommandError, match="already used"):
        tasks.set_status(service, owner, space, rid, request.model_copy(update={"status": "todo"}))
    with pytest.raises(CommandError) as error:
        tasks.set_status(service, owner, space, rid, move())
    assert error.value.status == 409


@pytest.mark.parametrize("identical", [False, True])
def test_concurrent_moves_have_one_revision_and_deterministic_retries(commands, identical):
    service, owner, space, _ = commands
    rid = UUID(task(service, owner, space)["id"])
    request = move()

    def attempt(index):
        change = request if identical else request.model_copy(update={"command_id": uuid4()})
        try:
            return tasks.set_status(service, owner, space, rid, change)["revision"]
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ([2, 2] if identical else [2, 409])
    assert service.get_resource(owner, space, rid)["revision"] == 2


def test_concurrent_column_creations_do_not_lose_updates(commands):
    service, owner, space, _ = commands

    def attempt(index):
        request = column().model_copy(update={"value": f"column_{index}", "label": str(index)})
        try:
            return tasks.add_column(service, owner, space, request)["revision"]
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [2, 409]
    assert len(tasks.columns(service, owner, space)["columns"]) == 4


def test_task_commands_enforce_tenant_and_readonly_agent_access(commands):
    service, owner, space, _ = commands
    rid = UUID(task(service, owner, space)["id"])
    reader = delegated(service, owner)
    assert tasks.columns(service, reader, space)["revision"] == 1
    other = replace(owner, subject=str(uuid4()))
    service.bootstrap(other)
    for identity, status in ((reader, 403), (other, 404)):
        for call in (
            lambda identity=identity: tasks.add_column(service, identity, space, column()),
            lambda identity=identity: tasks.set_status(service, identity, space, rid, move()),
        ):
            with pytest.raises(CommandError) as error:
                call()
            assert error.value.status == status
    with pytest.raises(CommandError):
        tasks.columns(service, other, space)


@pytest.mark.parametrize("operation", ["columns", "add_column", "set_status"])
def test_revocation_during_key_unwrap_prevents_disclosure_and_commit(
    commands,
    monkeypatch,
    operation,
):
    service, owner, space, _ = commands
    rid = UUID(task(service, owner, space)["id"])
    writer = delegated(service, owner, write=True)
    unwrap = service.keys.unwrap

    def revoke(*args):
        key = unwrap(*args)
        service.revoke(owner, UUID(writer.client_id))
        return key

    with monkeypatch.context() as patch:
        patch.setattr(service.keys, "unwrap", revoke)
        with pytest.raises(CommandError) as error:
            if operation == "columns":
                tasks.columns(service, writer, space)
            elif operation == "add_column":
                tasks.add_column(service, writer, space, column())
            else:
                tasks.set_status(service, writer, space, rid, move())
        assert error.value.status == 403
    assert tasks.columns(service, owner, space)["revision"] == 1
    assert service.get_resource(owner, space, rid)["revision"] == 1


def test_status_route_rejects_document_ids(commands):
    service, owner, space, _ = commands
    saved = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            expected_revision=0,
            kind="document",
            title="Not a task",
        ),
    )
    with pytest.raises(CommandError) as error:
        tasks.set_status(service, owner, space, UUID(saved["id"]), move())
    assert error.value.status == 404


def test_rest_and_mcp_share_columns_and_status_commands(commands, signing):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    space = UUID(service.bootstrap(owner)["id"])
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    saved = task(service, owner, space)
    writer = delegated(service, owner, write=True)
    agent_token = token(
        aud=verifier.resource, client_id=writer.client_id, space_grant_id=writer.grant_id
    )
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        agent_auth = {
            "Authorization": "Bearer " + agent_token,
            "X-Space-Agent-ID": writer.agent_id,
            "Accept": "application/json, text/event-stream",
        }

        def rpc(name, arguments):
            response = http.post(
                "/mcp/",
                headers=agent_auth,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {"space_id": str(space), **arguments}},
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("isError"), result
            return result["structuredContent"]

        path = f"/v1/spaces/{space}/tasks/columns"
        assert http.get(path, headers=auth).json() == rpc("task_columns", {})
        response = http.post(path, headers=auth, json=column().model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert rpc("task_columns", {}) == response.json()
        config = rpc(
            "add_task_column",
            {
                "change": tasks.AddColumn(
                    command_id=uuid4(),
                    expected_revision=2,
                    value="review",
                    label="Review",
                ).model_dump(mode="json")
            },
        )
        assert http.get(path, headers=auth).json() == config
        changed = rpc(
            "set_task_status",
            {
                "resource_id": saved["id"],
                "change": move().model_copy(update={"status": "backlog"}).model_dump(mode="json"),
            },
        )
        assert changed["status"] == "backlog" and changed["body"] == saved["body"]
        status_path = f"/v1/spaces/{space}/tasks/{saved['id']}/status"
        request = tasks.SetStatus(command_id=uuid4(), expected_revision=2, status="review")
        response = http.post(status_path, headers=auth, json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 3 and response.json()["status"] == "review"
        assert (
            http.post(status_path, headers=auth, json=request.model_dump(mode="json")).json()
            == response.json()
        )
        assert (
            http.post(status_path, headers=auth, json=move().model_dump(mode="json")).status_code
            == 409
        )
        assert http.get(path).status_code == 401


def test_agents_delete_tasks_but_not_documents_over_mcp(commands, signing):
    service = commands[0]
    verifier, token = signing
    owner = verifier.verify(token())
    space = UUID(service.bootstrap(owner)["id"])
    saved = task(service, owner, space)
    document = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(), expected_revision=0, kind="document", title="Keep", body="Kept."
        ),
    )
    writer = delegated(service, owner, write=True)
    agent_token = token(
        aud=verifier.resource, client_id=writer.client_id, space_grant_id=writer.grant_id
    )
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:

        def delete(resource_id, revision=1):
            response = http.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer " + agent_token,
                    "X-Space-Agent-ID": writer.agent_id,
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "delete_task",
                        "arguments": {
                            "space_id": str(space),
                            "resource_id": resource_id,
                            "expected_revision": revision,
                        },
                    },
                },
            )
            assert response.status_code == 200
            return response.json()["result"]

        refused = delete(document["id"])
        assert refused["isError"] and "tasks only" in refused["content"][0]["text"]
        assert delete(saved["id"], 2)["isError"]
        assert delete(saved["id"])["structuredContent"] == {"deleted": True}
    assert service.list_resources(owner, space, "task")["items"] == []
    assert service.get_resource(owner, space, UUID(document["id"]))["title"] == "Keep"


def test_status_quota_failure_rolls_back_head_index_and_receipt(commands):
    from space import quotas

    service, owner, space, connect = commands
    saved = task(service, owner, space)
    rid = UUID(saved["id"])
    usage = quotas.usage(service, owner, space)
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET storage_limit_bytes=retained_bytes WHERE space_id=%s",
            (space,),
        )
    request = move()
    with pytest.raises(CommandError, match="storage limit") as error:
        tasks.set_status(service, owner, space, rid, request)
    assert error.value.status == 429
    assert service.get_resource(owner, space, rid) == saved
    assert quotas.usage(service, owner, space)["retained_bytes"] == usage["retained_bytes"]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert connection.execute(
            "SELECT DISTINCT revision FROM space.resource_terms WHERE resource_id=%s",
            (rid,),
        ).fetchall() == [{"revision": 1}]
        assert not connection.execute(
            "SELECT id FROM space.commands WHERE id=%s",
            (request.command_id,),
        ).fetchone()
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET storage_limit_bytes=retained_bytes+1000000 "
            "WHERE space_id=%s",
            (space,),
        )
    assert tasks.set_status(service, owner, space, rid, request)["revision"] == 2
    charged = quotas.usage(service, owner, space)["retained_bytes"]
    assert charged > usage["retained_bytes"]
    tasks.set_status(service, owner, space, rid, request)
    assert quotas.usage(service, owner, space)["retained_bytes"] == charged


def test_column_limit_keeps_existing_values_and_allows_original_replay(commands):
    service, owner, space, _ = commands
    first = column()
    original = tasks.add_column(service, owner, space, first)
    for revision in range(2, 28):
        tasks.add_column(
            service,
            owner,
            space,
            tasks.AddColumn(
                command_id=uuid4(),
                expected_revision=revision,
                value=f"status_{revision}",
                label=f"Status {revision}",
            ),
        )
    assert len(tasks.columns(service, owner, space)["columns"]) == 30
    with pytest.raises(CommandError, match="at most 30"):
        tasks.add_column(
            service,
            owner,
            space,
            tasks.AddColumn(
                command_id=uuid4(),
                expected_revision=28,
                value="extra",
                label="Extra",
            ),
        )
    assert tasks.add_column(service, owner, space, first) == original


def test_receipts_cannot_be_reused_by_another_agent_or_another_task(commands):
    service, owner, space, _ = commands
    writer = delegated(service, owner, write=True)
    request = column()
    tasks.add_column(service, owner, space, request)
    with pytest.raises(CommandError, match="already used"):
        tasks.add_column(service, writer, space, request)
    first = UUID(task(service, owner, space)["id"])
    second = UUID(task(service, owner, space)["id"])
    request = move()
    tasks.set_status(service, owner, space, first, request)
    for identity, rid in ((writer, first), (owner, second)):
        with pytest.raises(CommandError, match="already used"):
            tasks.set_status(service, identity, space, rid, request)


@pytest.mark.parametrize("legacy", [False, True])
def test_custom_task_status_roundtrips_through_export_and_restore(commands, legacy):
    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, _ = commands
    tasks.add_column(service, owner, space, column().model_copy(update={"label": "À traiter"}))
    tasks.add_column(
        service,
        owner,
        space,
        tasks.AddColumn(
            command_id=uuid4(),
            expected_revision=2,
            value="waiting",
            label="Waiting",
        ),
    )
    saved = task(service, owner, space, status="backlog")
    archive = read_archive(service, owner, space)
    if legacy:
        archive.pop("task_columns")
    else:
        assert archive["task_columns"][-2:] == [
            {"value": "backlog", "label": "À traiter"},
            {"value": "waiting", "label": "Waiting"},
        ]
    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    assert not review["issues"]
    receipt = merge.commit(
        service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
    )
    assert receipt["state"] == "committed"
    restored = service.get_resource(target, target_space, UUID(saved["id"]))
    assert restored["status"] == "backlog" and restored["body"] == saved["body"]
    restored_columns = tasks.columns(service, target, target_space)["columns"]
    assert {"value": "backlog", "label": "backlog" if legacy else "À traiter"} in restored_columns
    if not legacy:
        assert {"value": "waiting", "label": "Waiting"} in restored_columns
    moved = tasks.set_status(service, target, target_space, UUID(saved["id"]), move())
    assert (
        tasks.set_status(
            service,
            target,
            target_space,
            UUID(saved["id"]),
            tasks.SetStatus(
                command_id=uuid4(),
                expected_revision=moved["revision"],
                status="backlog",
            ),
        )["status"]
        == "backlog"
    )


@pytest.mark.parametrize("conflict", ["label", "limit", "quota", "none"])
def test_restored_columns_preserve_destination_and_fail_atomically(commands, conflict):
    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import quotas
    from space import restore_merge as merge

    service, owner, space, connect = commands
    tasks.add_column(service, owner, space, column())
    saved = task(service, owner, space, status="backlog")
    archive = read_archive(service, owner, space)
    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    if conflict == "label":
        tasks.add_column(
            service,
            target,
            target_space,
            tasks.AddColumn(
                command_id=uuid4(),
                expected_revision=1,
                value="other",
                label="Backlog",
            ),
        )
    elif conflict == "limit":
        for revision in range(1, 28):
            tasks.add_column(
                service,
                target,
                target_space,
                tasks.AddColumn(
                    command_id=uuid4(),
                    expected_revision=revision,
                    value=f"local_{revision}",
                    label=f"Local {revision}",
                ),
            )
    elif conflict == "quota":
        quotas.consume_model_request(service, target, target_space)
        with connect() as connection:
            assert (
                connection.execute(
                    "UPDATE space.usage_limits SET storage_limit_bytes=1 WHERE space_id=%s",
                    (target_space,),
                ).rowcount
                == 1
            )
    else:
        tasks.add_column(
            service, target, target_space, column().model_copy(update={"label": "Local backlog"})
        )
    before = tasks.columns(service, target, target_space)
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    if conflict == "none":
        merge.commit(
            service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
        )
        assert service.get_resource(target, target_space, UUID(saved["id"]))["status"] == "backlog"
    else:
        with pytest.raises(
            CommandError,
            match={
                "label": "label conflicts",
                "limit": "30 status",
                "quota": "storage limit",
            }[conflict],
        ):
            merge.commit(
                service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
            )
        assert service.list_resources(target, target_space, "task")["items"] == []
    assert tasks.columns(service, target, target_space) == before


def test_archive_column_validation_rejects_unbounded_duplicate_and_invalid_options():
    for columns in (
        [{"value": "todo", "label": "To do"}] * 31,
        [{"value": "todo", "label": "To do"}, {"value": "todo", "label": "Other"}],
        [{"value": "todo", "label": "Same"}, {"value": "done", "label": "SAME"}],
        [{"value": "bad value", "label": "Bad"}],
        [{"value": "blank", "label": "   "}],
    ):
        with pytest.raises(ValueError):
            tasks.archive_columns(columns)
