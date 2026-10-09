import hashlib
import json
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive

from space import restore_staging as staging
from space.archive_schema import validate_archive
from space.security.auth import Unauthenticated
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def stage(service, owner, space, archive):
    text = json.dumps(archive, ensure_ascii=False)
    encoded = text.encode()
    request = staging.Start(
        id=uuid4(), sha256=hashlib.sha256(encoded).hexdigest(), bytes=len(encoded), parts=1
    )
    staging.start(service, owner, space, request)
    staging.upload(service, owner, space, request.id, 0, staging.Chunk(text=text))
    return request


def test_staging_is_immutable_scoped_and_complete(commands):
    service, owner, space, _ = commands
    service.save(owner, space, change())
    archive = read_archive(service, owner, space)
    request = stage(service, owner, space, archive)
    with staging.load(service, owner, space, request.id) as result:
        _, _, _, loaded, groups = result
        assert archive == loaded and len(groups) == 1
    assert staging.start(service, owner, space, request)["id"] == request.id
    staging.upload(
        service,
        owner,
        space,
        request.id,
        0,
        staging.Chunk(text=json.dumps(archive, ensure_ascii=False)),
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        payload = connection.execute(
            "SELECT encrypted_payload FROM space.restore_chunks"
        ).fetchone()["encrypted_payload"]
        assert b"Private body" in bytes(payload)  # staged chunks are readable JSON
    with pytest.raises(CommandError, match="different content"):
        staging.upload(service, owner, space, request.id, 0, staging.Chunk(text="changed"))
    with (
        pytest.raises(Unauthenticated),
        staging.load(service, delegated(service, owner, write=True), space, request.id),
    ):
        pytest.fail("Delegated callers cannot load an archive")
    staging.cancel(service, owner, space, request.id)
    with (
        pytest.raises(CommandError, match="expired"),
        staging.load(service, owner, space, request.id),
    ):
        pytest.fail("Cancelled archives cannot be loaded")


def test_untrusted_archive_cannot_hide_missing_versions_or_references(commands):
    service, owner, space, _ = commands
    first = service.save(owner, space, change())
    service.save(
        owner,
        space,
        change().model_copy(update={"resource_id": UUID(first["id"]), "expected_revision": 1}),
    )
    archive = read_archive(service, owner, space)
    validate_archive(archive)
    bad = deepcopy(archive)
    bad["entries"].pop(0)
    with pytest.raises(ValueError, match="missing or duplicate revisions"):
        validate_archive(bad)
    bad = deepcopy(archive)
    bad["entries"][-1]["value"]["recall"]["sources"] = [
        {"resource_id": str(uuid4()), "revision": 1}
    ]
    with pytest.raises(ValueError, match="referenced source"):
        validate_archive(bad)


def test_staging_bounds_actual_bytes_before_accepting_uploads(commands):
    service, owner, space, _ = commands
    request = staging.Start(id=uuid4(), sha256="a" * 64, bytes=1, parts=2)
    staging.start(service, owner, space, request)
    with pytest.raises(CommandError, match="declared size"):
        staging.upload(service, owner, space, request.id, 0, staging.Chunk(text="too big"))
    staging.upload(service, owner, space, request.id, 0, staging.Chunk(text="a"))
    with pytest.raises(CommandError, match="declared size"):
        staging.upload(service, owner, space, request.id, 1, staging.Chunk(text="b"))
    with (
        pytest.raises(CommandError, match="every archive part"),
        staging.load(service, owner, space, request.id),
    ):
        pytest.fail("Incomplete archives cannot be loaded")


@pytest.mark.parametrize("padding", [[[]] * 1000, [[0] * 1000], [[[[[[[]]]]]]]])
def test_review_and_commit_reject_complex_staged_json(commands, monkeypatch, padding):
    from space import archive_schema, restore_merge

    service, owner, space, _ = commands
    archive = read_archive(service, owner, space) | {"padding": padding}
    request = stage(service, owner, space, archive)
    monkeypatch.setattr(archive_schema, "MAX_ARCHIVE_CONTAINERS", 100)
    monkeypatch.setattr(archive_schema, "MAX_ARCHIVE_SEPARATORS", 100)
    monkeypatch.setattr(archive_schema, "MAX_ARCHIVE_DEPTH", 5)
    for operation, body in (
        (restore_merge.review, restore_merge.Review()),
        (restore_merge.commit, restore_merge.Commit(snapshot=0)),
    ):
        with pytest.raises(CommandError, match="exceeds supported limits") as error:
            operation(service, owner, space, request.id, body)
        assert error.value.status == 422
    assert read_archive(service, owner, space)["entries"] == []
