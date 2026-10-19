import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_memora_workspace import Models, claim_change, delegated
from test_memora_workspace import command_database as command_database
from test_memora_workspace import commands as commands
from test_memora_workspace import database as database
from test_memory_classification_workspace import assign, item
from test_relevance_workspace import decision_wire, wire
from test_workspace import change

from space import brain, workspace_items
from space.memory import RecallRequest, RememberClaims, SetMemoryLinks
from space.model_provider import MemoryModels
from space.settings import Settings
from space.workspace import CommandError


@pytest.fixture
def workspace(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    return service, owner, space


def remember(workspace, text, *, title="Recorded detail", **kwargs):
    service, owner, space = workspace
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(), title=title, claims=[{"index": "detail", "value": text}], **kwargs
        ),
    )


def vector_pool(monkeypatch, *memories):
    rows = [
        {
            "memory_id": UUID(memory["id"]),
            "claim_id": UUID(claim["id"]),
            "revision": memory["revision"],
            "entry_index": index,
            "score": 0.8,
        }
        for memory in memories
        for index, claim in enumerate(memory["memory"]["claims"])
    ]
    limits = []

    def search(_connection, _vector, _model, limit, **_kwargs):
        limits.append(limit)
        return rows[:limit]

    monkeypatch.setattr(brain, "search", search)
    return limits


def link(workspace, source, *targets):
    service, owner, space = workspace
    return service.set_memory_links(
        owner,
        space,
        SetMemoryLinks(
            command_id=uuid4(),
            resource_id=source["id"],
            expected_revision=source["revision"],
            links=[{"resource_id": target["id"], "relation": "expands_on"} for target in targets],
        ),
    )


def test_exact_code_outside_vectors_is_recovered_only_with_lexical_claims(workspace, monkeypatch):
    service, owner, space = workspace
    target = remember(workspace, "The release gate requires QZX771 before deployment.")
    distractor = remember(workspace, "The office serves lunch at noon.")
    limits = vector_pool(monkeypatch, distractor)
    calls = wire(
        service, monkeypatch, lambda c: "direct" if "QZX771" in c["text"] else "irrelevant"
    )
    request = RecallRequest(query="QZX771", strategy="semantic", limit=2)
    service.retrieval = replace(service.retrieval, lexical_claims=False)
    baseline = service.context(owner, space, request)
    assert baseline["items"] == [] and not baseline["found"]
    service.retrieval = replace(service.retrieval, lexical_claims=True)
    first = service.context(owner, space, request)
    second = service.context(owner, space, request)
    assert [i["id"] for i in first["items"]] == [target["id"]]
    assert first["items"] == second["items"]
    assert calls[1]["candidates"] == calls[2]["candidates"]
    assert max(len(c["candidates"]) for c in calls) <= 8
    assert len(set(limits)) == 1


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_fixed_negative_query_never_turns_pool_or_output_limit_into_support(
    workspace, monkeypatch, limit
):
    service, owner, space = workspace
    memories = [remember(workspace, f"Route {n} uses a car.") for n in range(12)]
    vector_pool(monkeypatch, *memories)
    calls = wire(service, monkeypatch, lambda _: "irrelevant")
    result = service.context(
        owner, space, RecallRequest(query="car insurance premium", limit=limit)
    )
    assert result["items"] == [] and not result["found"]
    assert len(calls) == 1
    assert len(calls[0]["candidates"]) <= min(limit * 4, 50 - limit)


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_lexical_and_vector_claims_share_one_deterministic_candidate_budget(
    workspace, monkeypatch, limit
):
    service, owner, space = workspace
    memories = [remember(workspace, f"QZX771 release requirement {n}.") for n in range(15)]
    vector_pool(monkeypatch, *reversed(memories))
    calls = wire(service, monkeypatch, lambda _: "direct")
    request = RecallRequest(query="QZX771", strategy="semantic", limit=limit)
    first = service.context(owner, space, request)
    second = service.context(owner, space, request)
    assert calls[0]["candidates"] == calls[1]["candidates"]
    assert len(calls[0]["candidates"]) == min(15, limit * 4, 50 - limit)
    assert len(first["items"]) <= limit
    assert first["items"] == second["items"]
    texts = [candidate["text"] for candidate in calls[0]["candidates"]]
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize(
    "invalid", [{"strategy": "lexical", "query": "detail"}, {"mode": "resume"}]
)
def test_link_expansion_rejects_unassessed_modes(invalid):
    with pytest.raises(ValidationError):
        RecallRequest(include_linked=True, **invalid)


def test_opt_in_one_hop_link_finds_detail_but_not_rejected_or_transitive_claims(
    workspace, monkeypatch
):
    service, owner, space = workspace
    far = remember(workspace, "The remote archive contains car receipts.")
    useful = remember(workspace, "The chosen vehicle is a blue car.")
    useful = link(workspace, useful, far)
    rejected = remember(workspace, "The office serves lunch at noon.")
    seed = remember(workspace, "Commute decision references an implementation detail.")
    seed = link(workspace, seed, useful, rejected)
    vector_pool(monkeypatch, seed)
    calls = wire(
        service, monkeypatch, lambda c: "direct" if "blue car" in c["text"] else "irrelevant"
    )
    request = RecallRequest(query="Commute decision", strategy="semantic", limit=3)
    ordinary = service.context(owner, space, request)
    assert ordinary["items"] == []
    result = service.context(owner, space, request.model_copy(update={"include_linked": True}))
    assert [i["id"] for i in result["items"]] == [useful["id"]]
    selected = result["items"][0]
    assert selected["reason"] == "memory_linked"
    assert seed["id"] in selected["linked_from"]
    assessed = {c["text"] for c in calls[-1]["candidates"]}
    assert rejected["memory"]["claims"][0]["value"] in assessed
    assert far["memory"]["claims"][0]["value"] not in assessed
    assert len(assessed) <= 12


def test_one_candidate_budget_keeps_direct_seed_instead_of_its_link(workspace, monkeypatch):
    service, owner, space = workspace
    target = remember(workspace, "A blue car is available.")
    seed = link(workspace, remember(workspace, "The commute uses a red car."), target)
    vector_pool(monkeypatch, seed)
    service.retrieval = replace(service.retrieval, max_candidates=1)
    calls = wire(service, monkeypatch, lambda _: "direct")
    result = service.context(
        owner,
        space,
        RecallRequest(query="commute", strategy="semantic", limit=1, include_linked=True),
    )
    assert [i["id"] for i in result["items"]] == [seed["id"]]
    assert len(calls[0]["candidates"]) == 1


def test_lexical_claims_work_with_disabled_provider_and_no_model_session(workspace, monkeypatch):
    service, owner, space = workspace
    target = remember(workspace, "The release gate is QZX771.")
    service.memory_models = MemoryModels(Settings(environment="test"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Lexical recall must not open a model session")

    monkeypatch.setattr(service.memory_models, "session", forbidden)
    result = service.context(owner, space, RecallRequest(query="QZX771", strategy="lexical"))
    assert [i["id"] for i in result["items"]] == [target["id"]]
    assert result["items"][0]["excerpt"] == "The release gate is QZX771."
    assert result["items"][0]["url"].endswith("#memory-" + target["memory"]["claims"][0]["id"])
    assert "assessment" not in result


@pytest.mark.parametrize("mutation", ["foreign", "stale_index", "forgotten"])
def test_lexical_claims_do_not_resurrect_ineligible_records(workspace, mutation):
    service, owner, space = workspace
    if mutation == "foreign":
        foreign = replace(owner, subject=str(uuid4()))
        foreign_space = UUID(service.bootstrap(foreign)["id"])
        remember((service, foreign, foreign_space), "The release gate is QZX771.")
    else:
        saved = remember(workspace, "The release gate is QZX771.")
        if mutation == "forgotten":
            service.change_memory(
                owner, space, claim_change(saved, saved["memory"]["claims"][0]["id"], "forget")
            )
        else:
            with service.db.transaction(owner) as connection:
                service._scope(connection, owner, space)
                connection.execute(
                    "UPDATE space.memory_claims SET revision=revision+1 WHERE memory_id=%s",
                    (saved["id"],),
                )
    result = service.context(owner, space, RecallRequest(query="QZX771", strategy="lexical"))
    assert result["items"] == [] and not result["found"]


def test_selected_source_passage_has_exact_offsets_citation_and_budgeted_diagnostics(
    workspace, monkeypatch
):
    service, owner, space = workspace
    body = "Unrelated weather bulletin. " * 20 + "Mira travels by car." + " End of bulletin." * 20
    source = service.save(
        owner, space, change().model_copy(update={"title": "Commute evidence", "body": body})
    )
    requests = decision_wire(service, monkeypatch)
    result = service.context(owner, space, RecallRequest(query="car", max_chars=2000))
    assert len(requests) == 1 and requests[0][0].startswith(
        "https://openrouter.ai/api/alpha/decisions"
    )
    units = requests[0][1]["state"]["candidates"]
    assert "".join(unit["text"] for unit in units) == body
    assert all(len(unit["text"]) <= 500 for unit in units)
    selected = result["items"][0]
    passage = selected["passage"]
    assert selected["id"] == source["id"] and selected["revision"] == 1
    assert selected["excerpt"] == body[passage["start"] : passage["end"]]
    assert "car" in selected["excerpt"]
    assert selected["url"].endswith("&revision=1")
    assert len(json.dumps(result, ensure_ascii=False)) <= 2000
    diagnostic = result["assessment"]
    assert set(diagnostic) == {
        "route",
        "candidate_count",
        "assessed_units",
        "selected_units",
        "fallback_reason",
    }
    assert diagnostic["candidate_count"] == 1
    assert diagnostic["assessed_units"] == len(units)
    assert diagnostic["selected_units"] == 1
    assert diagnostic["fallback_reason"] is None


def test_long_authored_claim_is_not_split_to_force_decision(workspace, monkeypatch):
    service, owner, space = workspace
    text = "Mira travels by car. " * 35
    saved = remember(workspace, text)
    vector_pool(monkeypatch, saved)
    requests = decision_wire(service, monkeypatch)
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert requests and all(
        not url.startswith("https://openrouter.ai/api/alpha/decisions") for url, _ in requests
    )
    assert result["items"][0]["excerpt"] == text
    assert "passage" not in result["items"][0]
    assert result["assessment"]["candidate_count"] == 1
    assert result["assessment"]["fallback_reason"]


def test_project_names_disambiguate_equal_claims_without_becoming_factual_support(
    workspace, monkeypatch
):
    service, owner, space = workspace
    section = item(service, owner, space, "section", "Engineering")
    category = item(service, owner, space, "category", "Deployment")
    projects = [
        item(service, owner, space, "project", name, section_id=section["id"])
        for name in ("Atlas", "Boreal")
    ]
    memories = []
    for project in projects:
        saved = remember(workspace, "The release requires two approvals.", title="Release policy")
        memories.append(
            service.change_memory(
                owner,
                space,
                assign(
                    saved,
                    scope="project",
                    project_ids=[project["id"]],
                    category_ids=[category["id"]],
                ),
            )
        )
    vector_pool(monkeypatch, *memories)

    def choose(candidate):
        context = candidate["evidence_context"]
        return "direct" if context["projects"] == ["Atlas"] else "irrelevant"

    calls = wire(service, monkeypatch, choose)
    result = service.context(owner, space, RecallRequest(query="Atlas release approvals"))
    assert [i["id"] for i in result["items"]] == [memories[0]["id"]]
    for candidate in calls[0]["candidates"]:
        assert candidate["text"] == "The release requires two approvals."
        context = candidate["evidence_context"]
        assert context["resource_title"] == memories[0]["title"]
        assert context["categories"] == ["Deployment"]
        assert context["sections"] == ["Engineering"]
        assert "PRIVATE CLASSIFICATION 991" not in json.dumps(candidate)
    wire(service, monkeypatch, lambda _: "irrelevant")
    unsupported = service.context(owner, space, RecallRequest(query="Atlas release price"))
    assert not unsupported["found"] and unsupported["items"] == []


@pytest.mark.parametrize("mutation", ["catalog", "revoke"])
def test_context_or_authority_changes_during_assessment_deny_release(
    workspace, monkeypatch, mutation
):
    service, owner, space = workspace
    project = item(service, owner, space, "project", "Atlas")
    saved = remember(workspace, "Mira travels by car.")
    saved = service.change_memory(
        owner, space, assign(saved, scope="project", project_ids=[project["id"]])
    )
    vector_pool(monkeypatch, saved)
    reader = delegated(service, owner)

    def race():
        if mutation == "revoke":
            service.revoke(owner, UUID(reader.client_id))
        else:
            workspace_items.save(
                service,
                owner,
                space,
                workspace_items.SaveItem(
                    command_id=uuid4(),
                    item_id=project["id"],
                    expected_revision=1,
                    kind="project",
                    data={"name": "Boreal"},
                ),
            )

    requests = decision_wire(service, monkeypatch, race)
    with pytest.raises(CommandError) as error:
        service.context(reader, space, RecallRequest(query="Atlas automobile"))
    assert error.value.status == (409 if mutation == "catalog" else 403)
    assert len(requests) == 1


def test_expanded_claim_with_changed_source_is_not_released_as_current_fact(workspace, monkeypatch):
    service, owner, space = workspace
    source = service.save(
        owner,
        space,
        change().model_copy(update={"title": "Vehicle", "body": "Mira travels by car."}),
    )
    target = remember(
        workspace, "Mira travels by car.", source={"resource_id": source["id"], "revision": 1}
    )
    seed = link(workspace, remember(workspace, "Commute decision detail."), target)
    vector_pool(monkeypatch, seed)
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(source["id"]),
                "expected_revision": 1,
                "title": "Vehicle",
                "body": "The commute choice has been withdrawn.",
            }
        ),
    )
    calls = wire(service, monkeypatch, lambda c: "direct" if "car" in c["text"] else "irrelevant")
    result = service.context(
        owner, space, RecallRequest(query="Commute decision", include_linked=True)
    )
    assert not result["found"] and result["items"] == []
    linked = next(c for c in calls[0]["candidates"] if "car" in c["text"])
    assert linked["stale"] is True
    assert linked["evidence_context"]["source_titles"] == ["Vehicle"]


def test_linked_candidates_are_capped_at_five_inside_memory_ceiling(workspace, monkeypatch):
    service, owner, space = workspace
    targets = [remember(workspace, f"Implementation detail number {n}.") for n in range(8)]
    seed = link(workspace, remember(workspace, "Commute decision."), *targets)
    vector_pool(monkeypatch, seed)
    calls = wire(service, monkeypatch, lambda _: "direct")
    result = service.context(
        owner, space, RecallRequest(query="Commute", limit=10, include_linked=True)
    )
    assert len(calls[0]["candidates"]) == 6
    assert len(result["items"]) <= 10
    assert len({c["text"] for c in calls[0]["candidates"]}) == 6


def test_source_split_overflow_falls_back_without_dropping_short_claim(workspace, monkeypatch):
    service, owner, space = workspace
    saved = remember(workspace, "Mira travels by car.")
    vector_pool(monkeypatch, saved)
    service.save(
        owner,
        space,
        change().model_copy(update={"title": "car archive", "body": "Archive car record. " * 1500}),
    )
    requests = decision_wire(service, monkeypatch)
    result = service.context(owner, space, RecallRequest(query="car"))
    assert requests and all(
        not url.startswith("https://openrouter.ai/api/alpha/decisions") for url, _ in requests
    )
    payload = json.loads(requests[0][1]["messages"][1]["content"])
    assert len(payload["candidates"]) == 2
    assert any(c["text"] == "Mira travels by car." for c in payload["candidates"])
    assert any(c["title"] == "car archive" for c in payload["candidates"])
    assert any(i["id"] == saved["id"] for i in result["items"])
    assert result["assessment"]["candidate_count"] == 2
    assert result["assessment"]["fallback_reason"]
