"""Documents that keep their original file, against an isolated PostgreSQL database."""

import base64
import hashlib
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import document_files
from space.document_files import MAX_PARTS, PART_BYTES, Commit, Part
from space.resource_deletion import delete_resource
from space.retention import purge as retention_purge
from space.security.auth import Unauthenticated
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def parts(raw):
    return [raw[start : start + PART_BYTES] for start in range(0, len(raw), PART_BYTES)]


def upload(service, owner, space, raw, file_id=None):
    file_id = file_id or uuid4()
    for number, chunk in enumerate(parts(raw)):
        document_files.upload_part(
            service, owner, space, file_id, number, Part(data=base64.b64encode(chunk).decode())
        )
    return file_id


def commit(file_id, raw, **values):
    return Commit(
        command_id=values.pop("command_id", uuid4()),
        file_id=file_id,
        name=values.pop("name", "Quarterly plan.pdf"),
        type=values.pop("type", "application/PDF"),
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        title="Quarterly plan",
        body="Revenue grew in the third quarter.",
        **values,
    )


def read(service, owner, space, document, revision=None):
    return b"".join(
        base64.b64decode(
            document_files.read_part(service, owner, space, UUID(document["id"]), number, revision)[
                "data"
            ]
        )
        for number in range(document["file"]["parts"])
    )


def retained(connect, space):
    # The Space's usage row appears with its first stored byte.
    with connect() as connection:
        row = connection.execute(
            "SELECT retained_bytes FROM space.usage_limits WHERE space_id=%s", (space,)
        ).fetchone()
    return row[0] if row else 0


def stored_parts(connect, space):
    with connect() as connection:
        return connection.execute(
            "SELECT part,plain_bytes,resource_id,content_bytes FROM space.file_parts "
            "WHERE space_id=%s ORDER BY part",
            (space,),
        ).fetchall()


def test_a_file_round_trips_through_stored_parts_and_lists_with_its_document(commands):
    service, owner, space, connect = commands
    raw = bytes(range(256)) * 9000  # Three parts, the last one partial.
    before = retained(connect, space)
    request = commit(upload(service, owner, space, raw), raw)
    saved = document_files.commit(service, owner, space, request)
    assert saved["file"] == {
        "id": str(request.file_id),
        "name": "Quarterly plan.pdf",
        "type": "application/pdf",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "parts": 3,
    }
    assert saved["body"] == "Revenue grew in the third quarter."
    assert read(service, owner, space, saved) == raw
    listed = service.list_resources(owner, space, "document")["items"]
    assert [item["file"] for item in listed] == [saved["file"]]
    assert service.get_resource(owner, space, UUID(saved["id"]))["file"] == saved["file"]
    # Parts are kept in the database, so no object is written.
    assert service.objects.values == {}
    # A part costs its own size: the stored bytes are exactly the part's bytes.
    rows = stored_parts(connect, space)
    assert [(row[0], row[1]) for row in rows] == [
        (0, PART_BYTES),
        (1, PART_BYTES),
        (2, len(raw) - 2 * PART_BYTES),
    ]
    assert [row[2] for row in rows] == [UUID(saved["id"])] * 3
    assert [row[3] for row in rows] == [row[1] for row in rows]
    assert retained(connect, space) - before >= len(raw)
    # Retrying the commit returns the same document instead of creating another.
    assert document_files.commit(service, owner, space, request)["id"] == saved["id"]


def test_part_retries_are_safe_and_different_bytes_are_refused(commands):
    service, owner, space, _ = commands
    raw = b"%PDF-1.7 small file"
    file_id = upload(service, owner, space, raw)
    upload(service, owner, space, raw, file_id)
    with pytest.raises(CommandError, match="different content") as error:
        upload(service, owner, space, b"%PDF-1.7 other bytes", file_id)
    assert error.value.status == 409
    saved = document_files.commit(service, owner, space, commit(file_id, raw))
    # A late retry of a committed part changes nothing.
    upload(service, owner, space, raw, file_id)
    assert read(service, owner, space, saved) == raw


@pytest.mark.parametrize(
    "data,status",
    [
        (base64.b64encode(b"x" * (PART_BYTES + 1)).decode(), 413),
        ("not base64!", 422),
    ],
)
def test_oversized_and_malformed_parts_are_refused(commands, data, status):
    service, owner, space, _ = commands
    with pytest.raises(CommandError) as error:
        document_files.upload_part(service, owner, space, uuid4(), 0, Part(data=data))
    assert error.value.status == status


def test_a_commit_needs_every_part_of_the_declared_size(commands):
    service, owner, space, connect = commands
    raw = b"y" * (PART_BYTES + 10)
    file_id = uuid4()
    first = parts(raw)[0]
    document_files.upload_part(
        service, owner, space, file_id, 0, Part(data=base64.b64encode(first).decode())
    )
    with pytest.raises(CommandError, match="incomplete or expired") as error:
        document_files.commit(service, owner, space, commit(file_id, raw))
    assert error.value.status == 409
    assert service.list_resources(owner, space, "document")["items"] == []
    upload(service, owner, space, raw, file_id)
    with connect() as connection:
        connection.execute("UPDATE space.file_parts SET created_at=now()-interval '55 minutes'")
    with pytest.raises(CommandError, match="incomplete or expired"):
        document_files.commit(service, owner, space, commit(file_id, raw))
    with pytest.raises(CommandError, match="incomplete or expired"):
        document_files.commit(service, owner, space, commit(uuid4(), raw))


def test_edits_keep_the_file_and_every_revision_reads_it(commands):
    service, owner, space, _ = commands
    raw = b"PK\x03\x04 presentation bytes"
    saved = document_files.commit(
        service, owner, space, commit(upload(service, owner, space, raw), raw)
    )
    edited = service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(saved["id"]),
                "expected_revision": 1,
                "title": "Renamed plan",
                "body": "Corrected text",
            }
        ),
    )
    assert edited["revision"] == 2 and edited["file"] == saved["file"]
    assert read(service, owner, space, edited) == raw
    assert read(service, owner, space, saved, revision=1) == raw
    plain = service.save(owner, space, change())
    assert "file" not in plain
    with pytest.raises(CommandError, match="no original file") as error:
        document_files.read_part(service, owner, space, UUID(plain["id"]), 0)
    assert error.value.status == 404
    with pytest.raises(CommandError, match="not found"):
        document_files.read_part(service, owner, space, UUID(saved["id"]), 1)


def test_deleting_the_document_removes_its_parts_and_frees_storage(commands):
    service, owner, space, connect = commands
    before = retained(connect, space)
    raw = b"z" * (PART_BYTES + 5)
    saved = document_files.commit(
        service, owner, space, commit(upload(service, owner, space, raw), raw)
    )
    assert delete_resource(service, owner, space, UUID(saved["id"]), 1) == {"deleted": True}
    assert stored_parts(connect, space) == []
    assert retained(connect, space) == before


def test_retention_removes_parts_no_document_committed(commands):
    service, owner, space, connect = commands
    before = retained(connect, space)
    raw = b"abandoned upload"
    kept = document_files.commit(
        service, owner, space, commit(upload(service, owner, space, raw), raw)
    )
    upload(service, owner, space, raw)
    with connect() as connection:
        connection.execute(
            "UPDATE space.file_parts SET created_at=now()-interval '2 hours' "
            "WHERE space_id=%s AND resource_id IS NULL",
            (space,),
        )
    with connect(admin=True) as connection:
        assert retention_purge(connection)["staged_file_parts"] >= 1
    assert [row[2] for row in stored_parts(connect, space)] == [UUID(kept["id"])]
    assert read(service, owner, space, kept) == raw
    assert retained(connect, space) > before


def test_only_a_signed_in_writer_uploads_files(commands):
    service, owner, space, _ = commands
    raw = b"agent bytes"
    file_id = upload(service, owner, space, raw)
    saved = document_files.commit(service, owner, space, commit(file_id, raw))
    for agent in (delegated(service, owner), delegated(service, owner, write=True)):
        with pytest.raises(Unauthenticated):
            upload(service, agent, space, raw)
        with pytest.raises(Unauthenticated):
            document_files.commit(service, agent, space, commit(file_id, raw))
        # Agents read the document and its file details, not the file's bytes over REST.
        assert service.get_resource(agent, space, UUID(saved["id"]))["file"] == saved["file"]


def test_names_and_media_types_are_validated():
    raw = b"x"
    valid = commit(uuid4(), raw).model_dump(mode="json")
    for name in ("../secret.pdf", "a\\b.pdf", "line\nbreak.pdf", "   "):
        with pytest.raises(ValueError):
            Commit.model_validate({**valid, "name": name})
    for media in ("application/pdf; charset=binary", "pdf", "text/<script>"):
        with pytest.raises(ValueError):
            Commit.model_validate({**valid, "type": media})
    assert commit(uuid4(), raw).type == "application/pdf"


def test_a_failed_upload_is_discarded_at_once_and_committed_files_are_untouched(commands):
    service, owner, space, connect = commands
    before = retained(connect, space)
    raw = b"q" * (PART_BYTES + 99)
    abandoned = upload(service, owner, space, raw)
    assert retained(connect, space) - before >= len(raw)
    assert document_files.discard(service, owner, space, abandoned) == {"discarded": 2}
    assert stored_parts(connect, space) == []
    assert retained(connect, space) == before
    with pytest.raises(CommandError, match="incomplete or expired"):
        document_files.commit(service, owner, space, commit(abandoned, raw))
    # Discarding a committed file, or one never uploaded, removes nothing.
    saved = document_files.commit(
        service, owner, space, commit(upload(service, owner, space, raw), raw)
    )
    assert document_files.discard(service, owner, space, UUID(saved["file"]["id"])) == {
        "discarded": 0
    }
    assert document_files.discard(service, owner, space, uuid4()) == {"discarded": 0}
    assert read(service, owner, space, saved) == raw
    with pytest.raises(Unauthenticated):
        document_files.discard(service, delegated(service, owner, write=True), space, uuid4())


def test_the_part_number_and_size_limits_follow_the_250_part_cap(commands):
    service, owner, space, _ = commands
    assert MAX_PARTS == 250 and document_files.MAX_FILE_BYTES == 250 * PART_BYTES
    with pytest.raises(CommandError, match="at most 250 parts") as error:
        document_files.upload_part(service, owner, space, uuid4(), MAX_PARTS, Part(data="QUFB"))
    assert error.value.status == 422
    document_files.upload_part(service, owner, space, uuid4(), MAX_PARTS - 1, Part(data="QUFB"))
    valid = commit(uuid4(), b"x").model_dump(mode="json")
    Commit.model_validate({**valid, "bytes": 250 * PART_BYTES})
    with pytest.raises(ValueError):
        Commit.model_validate({**valid, "bytes": 250 * PART_BYTES + 1})


def thumbnail_for(service, owner, space, raw):
    thumbnail_id = upload(service, owner, space, raw)
    return {
        "id": thumbnail_id,
        "type": "image/webp",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "width": 480,
        "height": 320,
    }


def test_a_thumbnail_is_kept_beside_the_file_and_read_on_its_own(commands):
    service, owner, space, connect = commands
    raw = b"\x89PNG picture bytes" * 1000
    small = b"RIFF\x00\x00\x00\x00WEBP thumbnail" * 100
    thumbnail = thumbnail_for(service, owner, space, small)
    request = commit(
        upload(service, owner, space, raw),
        raw,
        name="Garden.png",
        type="image/png",
        width=3000,
        height=2000,
        thumbnail=thumbnail,
    )
    saved = document_files.commit(service, owner, space, request)
    assert saved["file"]["width"] == 3000 and saved["file"]["height"] == 2000
    assert "duration" not in saved["file"]
    assert saved["file"]["thumbnail"] == {
        **thumbnail,
        "id": str(thumbnail["id"]),
        "parts": 1,
    }
    # Both files belong to the document; each is read under its own ID.
    rows = stored_parts(connect, space)
    assert sorted(row[1] for row in rows) == sorted([len(raw), len(small)])
    assert {row[2] for row in rows} == {UUID(saved["id"])}
    assert read(service, owner, space, saved) == raw
    small_read = document_files.read_part(
        service, owner, space, UUID(saved["id"]), 0, thumbnail=True
    )
    assert base64.b64decode(small_read["data"]) == small
    assert small_read["type"] == "image/webp"
    with pytest.raises(CommandError, match="not found"):
        document_files.read_part(service, owner, space, UUID(saved["id"]), 1, thumbnail=True)
    # Agents and the list see the same details.
    listed = service.list_resources(owner, space, "document")["items"]
    assert listed[0]["file"]["thumbnail"]["bytes"] == len(small)
    # Deleting the document removes the thumbnail's part with the file's.
    assert delete_resource(service, owner, space, UUID(saved["id"]), 1) == {"deleted": True}
    assert stored_parts(connect, space) == []


def test_a_file_without_a_thumbnail_says_so_and_a_missing_thumbnail_part_fails_the_commit(
    commands,
):
    service, owner, space, _ = commands
    raw = b"MP4 video bytes" * 500
    saved = document_files.commit(
        service,
        owner,
        space,
        commit(
            upload(service, owner, space, raw),
            raw,
            name="Talk.mp4",
            type="video/mp4",
            duration=154.5,
        ),
    )
    assert saved["file"]["duration"] == 154.5
    assert "thumbnail" not in saved["file"]
    with pytest.raises(CommandError, match="no thumbnail") as error:
        document_files.read_part(service, owner, space, UUID(saved["id"]), 0, thumbnail=True)
    assert error.value.status == 404
    # A thumbnail whose part was never uploaded rolls the whole commit back.
    orphan = {
        "id": uuid4(),
        "type": "image/jpeg",
        "bytes": 10,
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "width": 10,
        "height": 10,
    }
    with pytest.raises(CommandError, match="incomplete or expired"):
        document_files.commit(
            service,
            owner,
            space,
            commit(upload(service, owner, space, raw), raw, thumbnail=orphan),
        )
    assert len(service.list_resources(owner, space, "document")["items"]) == 1


def test_thumbnails_and_media_details_are_validated():
    valid = commit(uuid4(), b"x").model_dump(mode="json")
    thumbnail = {
        "id": str(uuid4()),
        "type": "image/webp",
        "bytes": 1000,
        "sha256": "a" * 64,
        "width": 480,
        "height": 320,
    }
    Commit.model_validate({**valid, "thumbnail": thumbnail})
    for bad in (
        {**thumbnail, "id": valid["file_id"]},
        {**thumbnail, "type": "image/png"},
        {**thumbnail, "bytes": document_files.THUMBNAIL_BYTES + 1},
        {**thumbnail, "width": 0},
        {**thumbnail, "height": document_files.THUMBNAIL_SIDE + 1},
    ):
        with pytest.raises(ValueError):
            Commit.model_validate({**valid, "thumbnail": bad})
    for detail, value in (("width", 0), ("height", -1), ("duration", 0), ("width", 1.5)):
        with pytest.raises(ValueError):
            Commit.model_validate({**valid, detail: value})
    assert Commit.model_validate({**valid, "duration": 0.5}).duration == 0.5
