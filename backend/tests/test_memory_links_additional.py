"""Parameters: disposable database fixtures and fake memory providers.
What it does: checks refill, explicit target races and provenance-independent node links.
Output: integration regression evidence without live services or provider calls.
"""

from uuid import UUID

import pytest
import test_memora_pipeline
from test_database_setup import database as database_fixture
from test_explicit_memory_links import assert_node_link, explicit
from test_memory_relationships_workspace import (
    RelatingModels,
    first_claim,
    links,
    remember_request,
    seed,
    write,
)
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import RecallMetadata
from space.resource_deletion import delete_memory
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


@pytest.mark.parametrize("rejection", ["excluded", "stale"])
def test_relationship_candidates_refill_after_five_ineligible_higher_ranked_hits(
    commands, monkeypatch, rejection
):
    service, owner, space, _ = commands

    def ranked_embedding(text):
        """Parameters: fixture text. What it does: ranks eligible lower. Output: unit vector."""
        return ([0.8, 0.6] if text.startswith("Eligible") else [1.0, 0.0]) + [0.0] * 1534

    monkeypatch.setattr(test_memora_pipeline, "fixture_embedding", ranked_embedding)
    document = service.save(owner, space, change())
    rejected = [
        seed(
            commands,
            text=f"Rejected private car route {n}.",
            source={"resource_id": document["id"], "revision": document["revision"]},
        )
        for n in range(5)
    ]
    eligible = [seed(commands, text=f"Eligible car route {n}.") for n in range(5)]
    if rejection == "stale":
        service.save(
            owner,
            space,
            change().model_copy(
                update={
                    "resource_id": UUID(document["id"]),
                    "expected_revision": document["revision"],
                    "body": "Corrected route source",
                }
            ),
        )
    else:
        # There is no public exclusion command; use the supported stored recall state.
        for resource in rejected:
            internal = service.get_resource(owner, space, UUID(resource["id"]), _internal=True)
            service._save(
                owner,
                space,
                change().model_copy(
                    update={
                        "resource_id": UUID(resource["id"]),
                        "expected_revision": resource["revision"],
                        "title": internal["title"],
                        "body": internal["body"],
                        "recall": RecallMetadata.model_validate(
                            {**internal["recall"], "excluded": True}
                        ),
                    }
                ),
                memory=internal["_memory"],
            )

    service.memory_models = models = RelatingModels(classify=False, relate=True)
    saved = write(service, owner, space, remember_request())
    assessments = [call[1] for call in models.calls if call[0] == "relate"]
    assert len(assessments) == 1
    assert {candidate["text"] for candidate in assessments[0]["state"]["candidates"]} == {
        first_claim(resource)["value"] for resource in eligible
    }
    outgoing = links(service, owner, space, saved)["links"]
    assert len(outgoing) == 5
    assert {link["resource_id"] for link in outgoing} == {resource["id"] for resource in eligible}
    assert all(link["origin"] == "inferred" for link in outgoing)
    for resource in rejected:
        assert links(service, owner, space, resource)["backlinks"] == []


def test_deleted_explicit_only_target_during_enrichment_aborts_without_partial_memory(commands):
    service, owner, space, _ = commands
    target = seed(commands)
    service.memory_models = models = RelatingModels(classify=False, relate=False)
    fired = []

    def delete_target():
        if not fired:
            fired.append(True)
            delete_memory(service, owner, space, UUID(target["id"]), target["revision"])

    models.before_call = delete_target
    request = remember_request(links=[explicit(target)])
    with pytest.raises(CommandError) as error:
        write(service, owner, space, request)
    assert fired
    assert error.value.status == 409
    assert service.list_memories(owner, space)["items"] == []
    assert not any(call[0] in {"relate", "classify", "model"} for call in models.calls)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_claims").fetchone()["n"] == 0
        )


def test_stale_source_provenance_hides_inferred_but_preserves_explicit_node_navigation(commands):
    service, owner, space, _ = commands
    document = service.save(owner, space, change())
    manual_target = seed(commands, text="Mira owns a car.")
    inferred_target = seed(commands, text="Mira commutes by car.")
    service.memory_models = RelatingModels(classify=False, relate=True)
    source = write(
        service,
        owner,
        space,
        remember_request(
            links=[explicit(manual_target)],
            source={"resource_id": document["id"], "revision": document["revision"]},
        ),
    )
    outgoing = links(service, owner, space, source)["links"]
    assert {(link["resource_id"], link["origin"]) for link in outgoing} == {
        (manual_target["id"], "explicit"),
        (inferred_target["id"], "inferred"),
    }
    assert len(links(service, owner, space, inferred_target)["backlinks"]) == 1

    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(document["id"]),
                "expected_revision": document["revision"],
                "body": "Corrected source document",
            }
        ),
    )
    outgoing = links(service, owner, space, source)["links"]
    incoming = links(service, owner, space, manual_target)["backlinks"]
    assert len(outgoing) == len(incoming) == 1
    assert_node_link(outgoing[0], manual_target)
    assert_node_link(incoming[0], source)
    assert links(service, owner, space, inferred_target)["backlinks"] == []
    assert service.get_resource(owner, space, UUID(source["id"]))["memory"]["links"] == [
        explicit(manual_target)
    ]
