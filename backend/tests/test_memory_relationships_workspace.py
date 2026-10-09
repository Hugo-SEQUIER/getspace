"""Incoming claim links use current scoped memory, not generated text or documents."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_memora_workspace import claim_change
from test_memory_classification import response
from test_memory_ingestion_concurrency import install_race
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import brain
from space.memory import RecallMetadata, RememberClaims, RememberMemory
from space.model_provider import TypeSafeError
from space.resource_deletion import delete_memory
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


class RelatingModels(Models):
    def __init__(self, *, classify=True, relate=True):
        super().__init__()
        self.settings = SimpleNamespace(
            memory_classification_provider="typesafe" if classify else "disabled",
            memory_relationship_provider="typesafe" if relate else "disabled",
        )
        self.scores = None
        self.failure = False
        self.before_relate = lambda: None
        self.after_relate = lambda: None

    def session(self, authorize):
        session = super().session(authorize)

        def evaluate(payload):
            if "catalog" in payload["state"]:
                authorize()
                self.calls.append(("classify", deepcopy(payload)))
                return response(payload, {"global": 0.99})
            assert "candidates" in payload["state"]
            self.before_relate()
            authorize()
            self.calls.append(("relate", deepcopy(payload)))
            if self.failure:
                raise TypeSafeError("Unavailable")
            scores = self.scores
            if scores is None:
                scores = {key: 0.97 for key in payload["questions"] if key.startswith("r")}
            result = response(payload, scores)
            self.after_relate()
            return result

        session.evaluate_typesafe = evaluate
        return session


def remember_request(operation="prepared", text="Mira commutes by automobile.", **values):
    common = {"command_id": uuid4(), "title": "Commute", **values}
    if operation == "raw":
        return RememberMemory(**common, text=text)
    return RememberClaims(**common, claims=[{"index": "Commute", "value": text}])


def write(service, owner, space, request):
    method = service.remember if isinstance(request, RememberMemory) else service.remember_claims
    return method(owner, space, request)


def seed(commands, *, text="Mira travels by car.", **values):
    service, owner, space, _ = commands
    service.memory_models = RelatingModels(classify=False, relate=False)
    return write(service, owner, space, remember_request(text=text, **values))


def first_claim(resource):
    return resource["memory"]["claims"][0]


def links(service, owner, space, resource):
    return service.memory_links(owner, space, UUID(resource["id"]))


def link_fields(focal, other):
    return {
        "entry_id": first_claim(focal)["id"],
        "resource_id": other["id"],
        "related_entry_id": first_claim(other)["id"],
        "revision": other["revision"],
        "relation": "related",
    }


def assert_link(actual, expected):
    assert {key: actual[key] for key in expected} == expected


@pytest.mark.parametrize("operation", ["prepared", "raw"])
def test_write_classifies_relates_and_persists_verbatim_with_backlink_and_retry(
    commands, operation
):
    service, owner, space, _ = commands
    previous = seed(commands)
    service.memory_models = models = RelatingModels()
    original = "Mira commutes by automobile.\nKeep this exact wording: café & <car>."
    request = remember_request(operation, original)
    saved = write(service, owner, space, request)
    claim = first_claim(saved)
    assert claim["value"] == original
    assert claim["classification"]["scope"] == "global"
    assert claim["relationships"] == {
        "status": "matched",
        "links": [
            {
                "resource_id": previous["id"],
                "entry_id": first_claim(previous)["id"],
                "revision": previous["revision"],
                "relation": "related",
                "confidence": 0.97,
                "source_text_hash": brain.text_hash(original).hex(),
                "target_text_hash": brain.text_hash(first_claim(previous)["value"]).hex(),
            }
        ],
    }
    assert [c[0] for c in models.calls] == ["embed", "classify", "relate"]
    assert [c[1] for c in models.calls if c[0] == "embed"] == [[original]]
    assert models.calls[-1][1]["state"]["memory"]["text"] == original
    loaded = service.get_resource(owner, space, UUID(saved["id"]))
    assert first_claim(loaded) == claim
    outgoing = links(service, owner, space, saved)
    incoming = links(service, owner, space, previous)
    assert len(outgoing["links"]) == len(incoming["backlinks"]) == 1
    assert_link(outgoing["links"][0], link_fields(saved, previous))
    assert_link(incoming["backlinks"][0], link_fields(previous, saved))
    assert outgoing["backlinks"] == incoming["links"] == []
    calls = deepcopy(models.calls)
    assert write(service, owner, space, request) == saved
    assert models.calls == calls
    unchanged = service.get_resource(owner, space, UUID(previous["id"]))
    assert first_claim(unchanged) == first_claim(previous)


@pytest.mark.parametrize("classify,relate", [(True, False), (False, True), (False, False)])
def test_relationship_provider_is_independent_opt_in(commands, classify, relate):
    service, owner, space, _ = commands
    seed(commands)
    service.memory_models = models = RelatingModels(classify=classify, relate=relate)
    saved = write(service, owner, space, remember_request())
    claim = first_claim(saved)
    assert claim["relationships"]["status"] == ("matched" if relate else "disabled")
    assert any(c[0] == "classify" for c in models.calls) == classify
    assert any(c[0] == "relate" for c in models.calls) == relate
    assert not any(c[0] == "model" for c in models.calls)


@pytest.mark.parametrize("failure,status", [(False, "no_match"), (True, "unavailable")])
def test_uncertain_or_unavailable_relation_preserves_all_claims_without_gpt_fallback(
    commands, failure, status
):
    service, owner, space, _ = commands
    seed(commands)
    service.memory_models = models = RelatingModels()
    models.failure = failure
    models.scores = {"r0": 0.89}
    request = RememberClaims(
        command_id=uuid4(),
        title="Two notes",
        claims=[
            {"index": "One", "value": "Mira commutes by automobile."},
            {"index": "Two", "value": "Mira owns a second car."},
        ],
    )
    saved = write(service, owner, space, request)
    assert [c["value"] for c in saved["memory"]["claims"]] == [c.value for c in request.claims]
    assert all(
        c["relationships"] == {"status": status, "links": []} for c in saved["memory"]["claims"]
    )
    assert not any(c[0] == "model" for c in models.calls)
    if failure:
        assert len([c for c in models.calls if c[0] == "relate"]) == 1


def test_uncertain_pair_does_not_suppress_independent_confident_pair(commands):
    service, owner, space, _ = commands
    candidates = [seed(commands, text=f"Mira travels by car on route {n}.") for n in range(2)]
    service.memory_models = models = RelatingModels()
    models.scores = {"r0": 0.55, "r1": 0.9}
    saved = write(service, owner, space, remember_request())
    payload = next(c[1] for c in models.calls if c[0] == "relate")
    assert set(payload["questions"]) == {"r0", "r1", "e0", "e1"}
    selected_text = payload["state"]["candidates"][1]["text"]
    selected = next(c for c in candidates if first_claim(c)["value"] == selected_text)
    result = first_claim(saved)["relationships"]
    assert result["status"] == "matched" and len(result["links"]) == 1
    assert result["links"][0]["resource_id"] == selected["id"]
    assert result["links"][0]["confidence"] == 0.9


def test_top_five_current_same_space_claims_only_and_embeddings_reused(commands):
    service, owner, space, _ = commands
    candidates = [seed(commands, text=f"Car commute route {n}.") for n in range(7)]
    service.save(owner, space, change().model_copy(update={"body": "CAR DOCUMENT SECRET 991"}))
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    write(service, other, other_space, remember_request(text="CAR OTHER SPACE SECRET 881"))
    service.memory_models = models = RelatingModels()
    request = remember_request()
    saved = write(service, owner, space, request)
    payloads = [c[1] for c in models.calls if c[0] == "relate"]
    assert len(payloads) == 1
    candidate_texts = [c["text"] for c in payloads[0]["state"]["candidates"]]
    assert len(candidate_texts) == 5
    assert set(candidate_texts) <= {first_claim(c)["value"] for c in candidates}
    assert len(first_claim(saved)["relationships"]["links"]) == 5
    assert [c[1] for c in models.calls if c[0] == "embed"] == [[request.claims[0].value]]
    encoded = json.dumps(payloads)
    assert "SECRET" not in encoded
    assert not any(c["id"] in encoded for c in candidates)


def test_empty_candidate_pool_skips_relationship_provider(commands):
    service, owner, space, _ = commands
    service.save(owner, space, change())
    service.memory_models = models = RelatingModels()
    saved = write(service, owner, space, remember_request())
    assert first_claim(saved)["relationships"] == {"status": "no_match", "links": []}
    assert not any(c[0] in {"relate", "model"} for c in models.calls)


@pytest.mark.parametrize("endpoint", ["outgoing", "incoming"])
@pytest.mark.parametrize("mutation", ["edit", "forget", "delete", "stale", "excluded"])
def test_changed_endpoints_remove_links_and_backlinks(commands, endpoint, mutation):
    service, owner, space, _ = commands
    source = service.save(owner, space, change())
    other_source = service.save(owner, space, change())
    previous = seed(commands, source={"resource_id": source["id"], "revision": 1})
    service.memory_models = RelatingModels()
    saved = write(
        service,
        owner,
        space,
        remember_request(source={"resource_id": other_source["id"], "revision": 1}),
    )
    assert links(service, owner, space, saved)["links"]
    affected = saved if endpoint == "outgoing" else previous
    if mutation == "stale":
        changed_source = other_source if endpoint == "outgoing" else source
        service.save(
            owner,
            space,
            change().model_copy(
                update={
                    "resource_id": UUID(changed_source["id"]),
                    "expected_revision": 1,
                    "body": "Changed source",
                }
            ),
        )
    elif mutation == "excluded":
        internal = service.get_resource(owner, space, UUID(affected["id"]), _internal=True)
        # There is no public exclusion command for memory; seed its supported stored state.
        service._save(
            owner,
            space,
            change().model_copy(
                update={
                    "resource_id": UUID(affected["id"]),
                    "expected_revision": affected["revision"],
                    "title": internal["title"],
                    "body": internal["body"],
                    "recall": RecallMetadata.model_validate(
                        {**internal["recall"], "excluded": True}
                    ),
                }
            ),
            memory=internal["_memory"],
        )
    elif mutation == "delete":
        delete_memory(service, owner, space, UUID(affected["id"]), affected["revision"])
    else:
        service.memory_models = RelatingModels(relate=False)
        service.change_memory(
            owner,
            space,
            claim_change(
                affected,
                first_claim(affected)["id"],
                mutation,
                value="Completely changed fact" if mutation == "edit" else None,
            ),
        )
    surviving = previous if endpoint == "outgoing" else saved
    result = links(service, owner, space, surviving)
    assert result["links"] == result["backlinks"] == []
    if mutation in {"stale", "excluded"}:
        focal = links(service, owner, space, affected)
        assert focal["links"] == focal["backlinks"] == []


@pytest.mark.parametrize("operation", ["prepared", "raw"])
@pytest.mark.parametrize("phase", ["disclosure", "commit"])
@pytest.mark.parametrize("mutation", ["edit", "forget", "delete", "revoke"])
def test_candidate_or_grant_changes_abort_before_disclosure_or_commit(
    commands, monkeypatch, operation, phase, mutation
):
    service, owner, space, _ = commands
    previous = seed(commands)
    service.memory_models = models = RelatingModels()
    writer = delegated(service, owner, write=True)
    fired = []

    def race():
        if fired:
            return
        fired.append(True)
        if mutation == "revoke":
            service.revoke(owner, UUID(writer.client_id))
        elif mutation == "delete":
            delete_memory(service, owner, space, UUID(previous["id"]), previous["revision"])
        else:
            service.change_memory(
                owner,
                space,
                claim_change(
                    previous,
                    first_claim(previous)["id"],
                    mutation,
                    value="Updated car route" if mutation == "edit" else None,
                ),
            )

    if phase == "disclosure":
        models.before_relate = race
    else:
        install_race(service, monkeypatch, "commit", race)
    request = remember_request(operation)
    with pytest.raises(CommandError) as error:
        write(service, writer, space, request)
    assert error.value.status == (403 if mutation == "revoke" else 409)
    assert fired
    if phase == "disclosure":
        assert not any(c[0] == "relate" for c in models.calls)
    assert all(r["id"] == previous["id"] for r in service.list_memories(owner, space)["items"])


@pytest.mark.parametrize("phase", ["disclosure", "commit"])
def test_unrelated_write_during_relationship_does_not_abort(commands, monkeypatch, phase):
    service, owner, space, _ = commands
    previous = seed(commands)
    service.memory_models = models = RelatingModels()
    fired = []

    def race():
        if not fired:
            fired.append(True)
            service.save(owner, space, change())

    if phase == "disclosure":
        models.before_relate = race
    else:
        install_race(service, monkeypatch, "commit", race)
    saved = write(service, owner, space, remember_request())
    assert fired
    assert first_claim(saved)["relationships"]["links"][0]["resource_id"] == previous["id"]


def test_memory_links_authorization_and_revocation_during_hydration(commands, monkeypatch):
    service, owner, space, _ = commands
    previous = seed(commands)
    service.memory_models = RelatingModels()
    write(service, owner, space, remember_request())
    stranger = replace(owner, subject=str(uuid4()))
    service.bootstrap(stranger)
    with pytest.raises(CommandError) as error:
        links(service, stranger, space, previous)
    assert error.value.status == 404
    reader = delegated(service, owner)
    assert links(service, reader, space, previous)["backlinks"]
    unwrap = service.keys.unwrap
    fired = []

    def revoke(*args):
        key = unwrap(*args)
        if not fired:
            fired.append(True)
            service.revoke(owner, UUID(reader.client_id))
        return key

    monkeypatch.setattr(service.keys, "unwrap", revoke)
    with pytest.raises(CommandError) as error:
        links(service, reader, space, previous)
    assert error.value.status == 403


def test_simultaneous_incoming_memories_can_commit_with_the_same_existing_candidate(commands):
    service, owner, space, _ = commands
    previous = seed(commands)
    service.memory_models = models = RelatingModels()
    barrier = Barrier(2)
    models.after_relate = lambda: barrier.wait(timeout=10)
    requests = [remember_request(text=f"Mira commutes by car via route {n}.") for n in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, service, owner, space, request) for request in requests]
        saved = [future.result(timeout=15) for future in futures]
    assert len({resource["id"] for resource in saved}) == 2
    assert all(
        first_claim(resource)["relationships"]["links"][0]["resource_id"] == previous["id"]
        for resource in saved
    )
    assert len(links(service, owner, space, previous)["backlinks"]) == 2
