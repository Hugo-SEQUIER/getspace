"""Saved resources are searchable in SQL without decrypting anything.

Coverage for the term index: the write path fingerprints a revision, an update replaces
the old fingerprints, the candidate query finds a resource by a title word alone, the row
level security policy isolates Spaces, and the SQL bound on a term array matches the
Python constant.

Psycopg runs in pipeline mode here, so a statement that violates a constraint reports
when the transaction completes, not when it is queued. Assertions wrap the transaction.
"""

from uuid import UUID, uuid4

import psycopg
import pytest
from test_database_setup import database as database_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import RecallMetadata, RecallRequest
from space.security.auth import Identity
from space.terms import (
    FIELD_BODY,
    FIELD_METADATA,
    MAX_INDEXED_TERMS,
    query_fingerprints,
    term_key,
)
from space.workspace import SaveResource

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture

TITLE = "Q3 planning"
TOPICS = ["planning", "Q3"]
BODY = "the refund policy changed for enterprise customers"

INSERT = (
    "INSERT INTO space.resource_terms(organization_id, space_id, resource_id, revision, "
    "field, terms, occurrences, tokens) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
)
MATCH = (
    "SELECT DISTINCT resource_id FROM space.resource_terms "
    "WHERE space_id=%s AND terms && %s::bytea[]"
)


def change(title=TITLE, body=BODY, **values):
    return SaveResource(
        command_id=uuid4(),
        expected_revision=0,
        kind="document",
        title=title,
        body=body,
        recall=RecallMetadata(category="source", topics=TOPICS),
        **values,
    )


def term_key_for(service, identity, space):
    _, scoped = service._key(identity, space)
    return term_key(scoped.key.material)


def rows_for(service, identity, space, resource_id):
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        return connection.execute(
            "SELECT field, cardinality(terms) AS terms, tokens FROM space.resource_terms "
            "WHERE resource_id=%s ORDER BY field",
            (resource_id,),
        ).fetchall()


def match(service, identity, space, key, words):
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        return connection.execute(MATCH, (space, query_fingerprints(words, key))).fetchall()


def test_a_saved_document_is_searchable_without_decrypting_it(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    key = term_key_for(service, identity, space)

    rows = rows_for(service, identity, space, saved["id"])
    assert [row["field"] for row in rows] == [FIELD_METADATA, FIELD_BODY]
    assert rows[0]["terms"] > 0 and rows[0]["tokens"] > 0
    assert rows[1]["terms"] > 0 and rows[1]["tokens"] > 0

    # A body word finds it, and so does a title word with no matching body word.
    assert [str(row["resource_id"]) for row in match(service, identity, space, key, "refund")] == [
        saved["id"]
    ]
    assert [
        str(row["resource_id"]) for row in match(service, identity, space, key, "planning")
    ] == [saved["id"]]
    assert match(service, identity, space, key, "no-such-word") == []


def test_an_update_replaces_the_fingerprints_of_the_old_revision(commands):
    service, identity, space, _ = commands
    first = service.save(identity, space, change(title="alpha rollout", body="alpha rollout"))
    key = term_key_for(service, identity, space)
    assert match(service, identity, space, key, "alpha") != []

    service.save(
        identity,
        space,
        change(title="beta rollout", body="beta rollout").model_copy(
            update={
                "resource_id": UUID(first["id"]),
                "expected_revision": first["revision"],
            }
        ),
    )
    assert match(service, identity, space, key, "alpha") == []
    assert [str(row["resource_id"]) for row in match(service, identity, space, key, "beta")] == [
        first["id"]
    ]
    assert len(rows_for(service, identity, space, first["id"])) == 2


def test_another_space_cannot_read_or_write_these_rows(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())

    other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])

    # Rows exist for the first Space, so an empty read here is isolation, not emptiness.
    with service.db.transaction(other) as connection:
        service._scope(connection, other, other_space)
        assert (
            connection.execute("SELECT count(*) AS rows FROM space.resource_terms").fetchone()[
                "rows"
            ]
            == 0
        )

    key = term_key_for(service, identity, space)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with service.db.transaction(other) as connection:
            scope = service._scope(connection, other, other_space)
            connection.execute(
                INSERT,
                (
                    scope["organization_id"],
                    space,
                    saved["id"],
                    saved["revision"],
                    FIELD_BODY,
                    query_fingerprints("refund", key),
                    [1],
                    1,
                ),
            )


def test_the_sql_term_bound_matches_the_python_bound(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    # A revision the write path does not use, so only the constraint is under test.
    revision = saved["revision"] + 5
    with service.db.transaction(identity) as connection:
        scope = service._scope(connection, identity, space)
        connection.execute(
            INSERT,
            (
                scope["organization_id"],
                space,
                saved["id"],
                revision,
                FIELD_BODY,
                [b"x" * 16] * MAX_INDEXED_TERMS,
                [1] * MAX_INDEXED_TERMS,
                MAX_INDEXED_TERMS,
            ),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        with service.db.transaction(identity) as connection:
            scope = service._scope(connection, identity, space)
            connection.execute(
                INSERT,
                (
                    scope["organization_id"],
                    space,
                    saved["id"],
                    revision,
                    FIELD_METADATA,
                    [b"y" * 16] * (MAX_INDEXED_TERMS + 1),
                    [1] * (MAX_INDEXED_TERMS + 1),
                    MAX_INDEXED_TERMS + 1,
                ),
            )


def test_recall_above_the_old_resource_cap_decrypts_only_candidates(commands, monkeypatch):
    """The Space-size cap is gone, and a recall pays for candidates, not for the corpus.

    The old code refused recall above 512 resources and decrypted the metadata of every
    resource it held. This Space is one resource past that cap: a selective query must
    still find its document, and must decrypt only what it selected.
    """
    service, identity, space, _ = commands
    old_cap = 513
    for index in range(old_cap):
        service.save(
            identity,
            space,
            change(title=f"note {index} needle{index}", body=f"body of note {index}"),
        )
    decrypted = {"metadata": 0}
    original = service._unpack

    def counting(key, scope, resource_id, revision, purpose, value):
        if purpose == "metadata":
            decrypted["metadata"] += 1
        return original(key, scope, resource_id, revision, purpose, value)

    monkeypatch.setattr(service, "_unpack", counting)
    result = service.context(identity, space, RecallRequest(query=f"needle{old_cap - 1}"))

    assert result["found"] is True
    assert any(f"needle{old_cap - 1}" in (item["title"] or "") for item in result["items"])
    # One candidate matched, so one metadata payload was read, not 513.
    assert decrypted["metadata"] < 16, decrypted


def test_candidate_selection_weights_a_rare_word_above_common_ones(commands):
    """A query mixing a rare word with common ones must not be filled by the common ones.

    Candidate selection weights each matched query word by how many resources hold it, so a
    resource carrying the distinctive word outranks resources that carry two common ones.
    Counting matched words instead would pick a common-word document here, and a bound of
    one leaves no room for that to pass unnoticed.
    """
    service, identity, space, _ = commands
    rare = service.save(identity, space, change(title="refund memo", body="refund memo"))
    for index in range(5):
        service.save(
            identity,
            space,
            change(
                title=f"policy enterprise note {index}",
                body=f"policy enterprise note {index}",
            ),
        )
    _, scoped = service._key(identity, space)
    key = term_key(scoped.key.material)
    terms = query_fingerprints("refund policy enterprise", key)

    picked = service._candidates(identity, space, terms, 1)
    assert [str(row[0]) for row in picked] == [rare["id"]]

    # The rare word is still found when the caller asks for more than the ring of matches.
    every = service._candidates(identity, space, terms, 10)
    assert str(rare["id"]) in [str(row[0]) for row in every]


def test_unrelated_words_cannot_improve_candidate_rank(commands):
    service, identity, space, _ = commands
    rare = service.save(identity, space, change(title="refund", body="refund"))
    for _ in range(5):
        service.save(identity, space, change(title="policy enterprise", body="policy enterprise"))
    clutter = "policy enterprise apple banana carrot dolphin elephant flamingo giraffe"
    service.save(identity, space, change(title=clutter, body=clutter))
    key = term_key_for(service, identity, space)
    picked = service._candidates(
        identity, space, query_fingerprints("refund policy enterprise", key), 1
    )
    assert str(picked[0][0]) == rare["id"]
    # A term repeated across title and body still counts as one resource occurrence.
    assert picked[0][2] == 1.0
