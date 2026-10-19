"""The browser can detect content changes without fetching encrypted content."""

import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_teams import create_team, join
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_items import project

from space import read_version, resource_deletion, tasks, teams, workspace_items
from space.app import create_app
from space.content_changes import KINDS
from space.memory import RememberClaims
from space.security.auth import Identity
from space.settings import Settings
from space.workspace import CommandError, Workspace

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def stub_workspace(*, role="member", visibility="shared", revision=2**63 - 1):
    connection = Mock()
    scope = {
        "organization_id": uuid4(),
        "space_id": uuid4(),
        "kind": "team",
        "default_space_id": uuid4(),
        "principal_id": uuid4(),
        "role": role,
    }
    connection.execute.return_value.fetchone.side_effect = [
        scope,
        {"role": role},
        {"content_revision": revision, "change_revisions": {"memory": 7}, "visibility": visibility},
    ]

    @contextmanager
    def transaction(identity):
        identity.check()
        yield connection

    service = Workspace(SimpleNamespace(transaction=transaction), None, None, "https://api/mcp/")
    return service, scope, connection


def test_version_is_exact_and_reads_no_keys_content_or_manifests():
    service, scope, connection = stub_workspace()
    owner = Identity("https://identity.invalid", str(uuid4()), int(time.time()) + 600)
    result = read_version.snapshot(service, owner, scope["space_id"])
    assert result["version"] == "1:9223372036854775807:member:shared"
    assert result["versions"] == {
        kind: f"2:{kind}:{7 if kind == 'memory' else 0}:member:shared" for kind in KINDS
    }
    queries = [call.args[0] for call in connection.execute.call_args_list]
    assert queries[-1] == (
        "SELECT content_revision,change_revisions,visibility FROM space.spaces "
        "WHERE organization_id=%s AND id=%s"
    )
    assert connection.execute.call_args.args[1] == (scope["organization_id"], scope["space_id"])
    lock = next(i for i, query in enumerate(queries) if "pg_advisory_xact_lock_shared" in query)
    recheck = next(
        i for i, query in enumerate(queries) if query.startswith("SELECT space.space_role")
    )
    assert lock < recheck < len(queries) - 1
    assert len(queries) == 6
    assert all("encrypted" not in query and "space_keys" not in query for query in queries)


@pytest.mark.parametrize("missing", ["scope", "role", "space"])
def test_removed_access_never_returns_a_version(missing):
    service, scope, connection = stub_workspace()
    rows = {
        "scope": [None],
        "role": [scope, {"role": None}],
        "space": [scope, {"role": "member"}, None],
    }
    connection.execute.return_value.fetchone.side_effect = rows[missing]
    owner = Identity("https://identity.invalid", str(uuid4()), int(time.time()) + 600)
    with pytest.raises(CommandError) as error:
        read_version.snapshot(service, owner, scope["space_id"])
    assert error.value.status == 404
    if missing != "space":
        assert all(
            "content_revision" not in call.args[0] for call in connection.execute.call_args_list
        )


def test_rest_requires_browser_auth_and_explicit_uuid_and_preserves_access_errors(signing):
    service, scope, connection = stub_workspace(role="viewer", revision=2**53 + 1)
    verifier, token = signing
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + token()}
    endpoint = f"/v1/spaces/{scope['space_id']}/read-version"
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        assert http.get(endpoint).status_code == 401
        assert http.get("/v1/spaces/not-a-uuid/read-version", headers=auth).status_code == 422
        agent = token(aud=verifier.resource, client_id=str(uuid4()), space_grant_id=str(uuid4()))
        assert http.get(endpoint, headers={"Authorization": "Bearer " + agent}).status_code == 401
        connection.execute.assert_not_called()
        response = http.get(endpoint, headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["version"] == "1:9007199254740993:viewer:shared"
        assert response.json()["versions"]["memory"] == "2:memory:7:viewer:shared"
        assert response.headers["Cache-Control"] == "no-store"
        connection.execute.return_value.fetchone.side_effect = [None]
        assert http.get(endpoint, headers=auth).status_code == 404


def test_rest_bootstrap_advertises_read_version_without_mutating_shared_response(signing):
    service, scope, _ = stub_workspace()
    shared = {"id": str(scope["space_id"]), "role": scope["role"]}
    service.bootstrap = Mock(return_value=shared)
    service.info = Mock(return_value=shared)
    verifier, token = signing
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + token()}
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        assert http.post("/v1/workspace").status_code == 401
        service.bootstrap.assert_not_called()
        response = http.post("/v1/workspace", headers=auth)
        assert response.status_code == 200, response.text
        assert response.json() == {**shared, "read_version_supported": True}
        assert http.get("/v1/workspace", headers=auth).json() == shared
    assert shared == {"id": str(scope["space_id"]), "role": scope["role"]}
    service.bootstrap.assert_called_once()


def test_committed_content_changes_version_and_retries_preserve_it(commands):
    service, owner, space, _ = commands
    initial = read_version.snapshot(service, owner, space)
    document = change()
    service.save(owner, space, document)
    previous = read_version.snapshot(service, owner, space)
    assert previous != initial
    service.save(owner, space, document)
    assert read_version.snapshot(service, owner, space) == previous

    workspace_items.save(service, owner, space, project())
    current = read_version.snapshot(service, owner, space)
    assert current != previous
    previous = current
    tasks.add_column(
        service,
        owner,
        space,
        tasks.AddColumn(command_id=uuid4(), expected_revision=1, value="blocked", label="Blocked"),
    )
    current = read_version.snapshot(service, owner, space)
    assert current != previous
    service.memory_models = Models()
    service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Preference",
            claims=[{"index": "Database", "value": "Use PostgreSQL."}],
        ),
    )
    assert read_version.snapshot(service, owner, space) != current


def test_team_role_visibility_and_removal_invalidate_reads(commands):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    org, home = UUID(team["organization_id"]), UUID(team["id"])
    member, _ = join(service, owner, team)
    member_id = UUID(
        next(row["id"] for row in teams.members(service, member, org)["items"] if row["is_self"])
    )
    before = read_version.snapshot(service, member, home)
    teams.change_member(service, owner, org, member_id, "viewer")
    after = read_version.snapshot(service, member, home)
    assert before != after
    assert before["version"].split(":")[1] == after["version"].split(":")[1]

    selected = UUID(
        teams.create_space(
            service,
            owner,
            org,
            teams.CreateSpace(command_id=uuid4(), name="Selected", visibility="shared"),
        )["id"]
    )
    before = read_version.snapshot(service, owner, selected)
    teams.set_access(service, owner, selected, teams.SpaceAccess(visibility="private"))
    after = read_version.snapshot(service, owner, selected)
    assert before != after
    assert before["version"].split(":")[1] == after["version"].split(":")[1]
    with pytest.raises(CommandError) as error:
        read_version.snapshot(service, member, selected)
    assert error.value.status == 404

    teams.change_member(service, owner, org, member_id)
    with pytest.raises(CommandError) as error:
        read_version.snapshot(service, member, home)
    assert error.value.status == 404


def test_version_respects_tenant_fencing_and_revoked_grants(commands):
    service, owner, space, _ = commands
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    with pytest.raises(CommandError) as error:
        read_version.snapshot(service, other, space)
    assert error.value.status == 404
    before = read_version.snapshot(service, owner, space)
    service.save(other, other_space, change())
    assert read_version.snapshot(service, owner, space) == before

    reader = delegated(service, owner)
    assert read_version.snapshot(service, reader, space) == read_version.snapshot(
        service, owner, space
    )
    service.revoke(owner, UUID(reader.client_id))
    with pytest.raises(CommandError) as error:
        read_version.snapshot(service, reader, space)
    assert error.value.status == 403


def test_each_write_changes_only_the_kinds_its_lists_show(commands):
    service, owner, space, _ = commands

    def changed(action):
        before = read_version.snapshot(service, owner, space)
        action()
        after = read_version.snapshot(service, owner, space)
        assert after["version"] != before["version"]
        return {kind for kind in KINDS if after["versions"][kind] != before["versions"][kind]}

    saved = {}
    assert changed(lambda: saved.update(service.save(owner, space, change()))) == {
        "document",
        "memory",  # Memory listings show whether a source changed.
    }
    item = {}
    assert changed(lambda: item.update(workspace_items.save(service, owner, space, project()))) == {
        "project"
    }
    assert changed(
        lambda: tasks.add_column(
            service,
            owner,
            space,
            tasks.AddColumn(
                command_id=uuid4(), expected_revision=1, value="blocked", label="Blocked"
            ),
        )
    ) == {"columns"}
    service.memory_models = Models()
    assert changed(
        lambda: service.remember_claims(
            owner,
            space,
            RememberClaims(
                command_id=uuid4(),
                title="Preference",
                claims=[{"index": "Database", "value": "Use PostgreSQL."}],
            ),
        )
    ) == {"memory"}
    assert changed(
        lambda: resource_deletion.delete_resource(
            service, owner, space, UUID(saved["id"]), saved["revision"]
        )
    ) == {"document", "memory"}
