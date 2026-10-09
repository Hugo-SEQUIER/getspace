import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from pydantic import ValidationError

from space.memory import (
    ENGINE,
    MAX_LEXICAL_BODIES,
    MAX_LEXICAL_CANDIDATES,
    MAX_MEMORY_LIST,
    MAX_MEMORY_LOAD,
    ChangeMemory,
    RecallMetadata,
    RecallRequest,
    RememberMemory,
    SaveHandoff,
    bounded_result,
    rank_lexical,
)


def resource(title, body, *, topics=(), category="source", author="agent"):
    return {
        "id": str(uuid4()),
        "revision": 1,
        "title": title,
        "body": body,
        "kind": "document",
        "status": "todo",
        "updated_at": "2026-09-12T00:00:00Z",
        "author": author,
        "recall": RecallMetadata(category=category, topics=list(topics)).model_dump(mode="json"),
    }


def item(source, reason="matched", *, body=None, **values):
    """One ranked item as Workspace._item presents it to output shaping."""
    return {
        "id": source["id"],
        "revision": source["revision"],
        "kind": source["kind"],
        "title": source["title"],
        "status": source["status"],
        "updated_at": source["updated_at"],
        "author": source["author"],
        "category": source["recall"]["category"],
        "sources": source["recall"]["sources"],
        "reason": reason,
        "body": source["body"] if body is None else body,
        "stale": False,
        "verification": "handoff" if source["recall"]["category"] == "handoff" else "user_authored",
        **values,
    }


def url(id_, revision):
    return f"https://space.test/{id_}/{revision}"


def test_lexical_ranking_orders_shared_terms_and_drops_unrelated_rows():
    alpha = resource("Deploy preview", "Waiting for approval", topics=["website"])
    zeta = resource("Dinner", "Book a table", topics=["home"])
    rows = [(alpha, "website approval website"), (zeta, "website")]
    ranked = rank_lexical(rows, "website approval")
    assert [source["id"] for source, _, _ in ranked] == [alpha["id"], zeta["id"]]
    assert all(score > 0 for _, _, score in ranked)
    assert rank_lexical(rows, "unrelated-telescope") == []
    assert rank_lexical(rows, "   ") == []
    assert rank_lexical([], "website") == []
    # Shared terms never merge rows and identical input ranks identically.
    once = rank_lexical(rows, "website")
    assert {source["id"] for source, _, _ in once} == {alpha["id"], zeta["id"]}
    assert once == rank_lexical(rows, "website")


def test_match_near_end_of_long_source_is_retrievable_and_excerpted():
    source = resource("Long source", "background " * 20000 + "zebra milestone complete")
    request = RecallRequest(query="zebra", max_chars=2000)
    ranked = rank_lexical([(source, source["body"])], request.query)
    assert [entry["id"] for entry, _, _ in ranked] == [source["id"]]
    result = bounded_result([item(entry) for entry, _, _ in ranked], request, url)
    assert "zebra" in result["items"][0]["excerpt"]
    assert result["items"][0]["truncated"]


def test_context_budget_covers_escaped_text_and_provenance():
    request = RecallRequest(mode="resume", max_chars=2000)
    for body in [
        '"\
'
        * 10000,
        "안녕하세요 " * 10000,
        "x" * 10000,
    ]:
        source = resource("Handoff", body, category="handoff")
        result = bounded_result([item(source, "recent_handoff")], request, url)
        assert len(json.dumps(result, ensure_ascii=False)) <= request.max_chars
        if result["items"]:
            assert result["items"][0]["verification"] == "handoff"
            assert result["items"][0]["revision"] == 1
    source = resource("Notes", "Ordinary body")
    result = bounded_result([item(source)], RecallRequest(query="body"), url)
    assert result["items"][0]["verification"] == "user_authored"
    assert result["items"][0]["stale"] is False


def test_related_framing_fits_the_budget_without_changing_a_full_ranked_answer():
    request = RecallRequest(query="body", max_chars=2000)
    ranked = item(resource("Notes", "body"))
    baseline = bounded_result([ranked], request, url)
    # Leave only five characters; even the optional tier's empty framing will not fit.
    ranked["body"] += "x" * (request.max_chars - len(json.dumps(baseline)) - 5)
    baseline = bounded_result([ranked], request, url)
    assert len(json.dumps(baseline)) == request.max_chars - 5
    with_related = bounded_result(
        [ranked],
        request.model_copy(update={"include_related": True}),
        url,
        related=[item(resource("Related", "body"))],
    )
    assert len(json.dumps(with_related, ensure_ascii=False)) <= request.max_chars
    assert with_related == baseline


def test_related_budget_includes_empty_recall_guidance():
    request = RecallRequest(query="body", max_chars=2000, include_related=True)
    # The response still needs its empty-recall note when related claims are present.
    for length in range(1100, 1550, 25):
        result = bounded_result([], request, url, related=[item(resource("Related", "x" * length))])
        assert result["found"] is False and result["note"]
        assert len(json.dumps(result, ensure_ascii=False)) <= request.max_chars


def test_memory_claim_items_carry_entry_identity_citations_and_no_review_state():
    entry_id = str(uuid4())
    source = resource("Commute source", "Mira travels by car.", category="memory")
    claim = item(
        source,
        "memory_semantic",
        entry_id=entry_id,
        title="Mira commute",
        memory_type="factual",
        edited=True,
        verification="memory",
    )
    result = bounded_result([claim], RecallRequest(query="automobile", strategy="semantic"), url)
    presented = result["items"][0]
    assert presented["verification"] == "memory"
    assert presented["entry_id"] == entry_id
    assert presented["memory_type"] == "factual"
    assert presented["edited"] is True
    assert presented["url"].endswith("#memory-" + entry_id)
    assert "review_state" not in presented
    assert result["engine"] == ENGINE
    assert result["items"][0]["excerpt"] == "Mira travels by car."


def test_bounded_result_reports_truncation_and_links_cited_sources():
    source = resource("Handoff", "Body")
    source["recall"]["sources"] = [{"resource_id": str(uuid4()), "revision": 3}]
    request = RecallRequest(mode="resume", limit=1)
    result = bounded_result(
        [item(source, "recent_handoff"), item(resource("Older", "Body"), "recent_handoff")],
        request,
        url,
    )
    assert [entry["id"] for entry in result["items"]] == [source["id"]]
    assert result["has_more"] is True
    assert result["items"][0]["sources"][0]["url"] == url(
        source["recall"]["sources"][0]["resource_id"], 3
    )
    # Empty recall is a bounded answer, never invented context.
    empty = bounded_result([], RecallRequest(mode="resume"), url)
    assert empty["items"] == [] and empty["has_more"] is False


def test_empty_recall_names_the_outcome_and_tells_the_caller_not_to_invent():
    search = bounded_result([], RecallRequest(query="unrelated topic"), url)
    assert search["found"] is False
    assert search["items"] == [] and search["has_more"] is False
    assert "no supported memory" in search["note"]
    assert "Do not invent" in search["note"]

    resume = bounded_result([], RecallRequest(mode="resume"), url)
    assert resume["found"] is False
    assert "nothing to resume" in resume["note"]

    source = resource("Notes", "Ordinary body")
    found = bounded_result([item(source)], RecallRequest(query="body"), url)
    assert found["found"] is True
    assert "note" not in found


def test_input_validation_and_explicit_handoff_structure():
    with pytest.raises(ValidationError):
        RecallRequest(query="   ")
    with pytest.raises(ValidationError):
        RecallRequest(mode="resume", strategy="semantic")
    with pytest.raises(ValidationError):
        RecallRequest(mode="resume", strategy="prompt")
    with pytest.raises(ValidationError):
        SaveHandoff(command_id=uuid4(), resource_id=uuid4(), title="Handoff", summary="Summary")
    with pytest.raises(ValidationError):
        RecallMetadata(topics=["cue||injection"])
    note = SaveHandoff(
        command_id=uuid4(), title="Handoff", summary="Summary", next_actions=["Test recall"]
    )
    assert "## Next actions\n- Test recall" in note.body()


def test_remember_and_change_requests_are_bounded_and_consistent():
    remembered = RememberMemory(command_id=uuid4(), title="Transport", text="Mira travels by car.")
    assert remembered.memory_type == "chat" and remembered.source is None
    with pytest.raises(ValidationError):
        RememberMemory(command_id=uuid4(), title="Transport", text="   ")
    with pytest.raises(ValidationError):
        RememberMemory(command_id=uuid4(), title="Transport", text="x" * 12001)
    edit = ChangeMemory(
        command_id=uuid4(),
        resource_id=uuid4(),
        expected_revision=1,
        entry_id=uuid4(),
        action="edit",
        value="Mira takes the train.",
    )
    assert edit.action == "edit" and edit.value == "Mira takes the train."
    forget = ChangeMemory(
        command_id=uuid4(),
        resource_id=edit.resource_id,
        expected_revision=1,
        entry_id=edit.entry_id,
        action="forget",
    )
    assert forget.action == "forget" and forget.value is None
    with pytest.raises(ValidationError):
        ChangeMemory.model_validate({**edit.model_dump(), "action": "edit", "value": None})
    with pytest.raises(ValidationError):
        ChangeMemory.model_validate({**edit.model_dump(), "action": "forget", "value": "Hidden"})
    with pytest.raises(ValidationError):
        ChangeMemory.model_validate({**edit.model_dump(), "expected_revision": 0})


def test_brain_bounds_are_explicit():
    assert ENGINE == "memora-brain-lexical-v1"
    assert MAX_LEXICAL_CANDIDATES == 256
    # A search reads more candidates than bodies, and lists more memories than it loads.
    assert MAX_LEXICAL_BODIES < MAX_LEXICAL_CANDIDATES
    assert MAX_MEMORY_LOAD < MAX_MEMORY_LIST


def test_retrieval_defaults_reproduce_shipped_behaviour():
    from space import brain

    config = brain.DEFAULT_RETRIEVAL
    # The baseline must be the product as shipped, so measuring it needs no
    # special case: no session setting is changed and the candidate maths holds.
    assert config.ef_search is None
    assert config.iterative_scan == "off"
    assert config.scan_multiplier is None
    assert config.max_scan_tuples is None
    assert config.expansion_order == "index"
    assert config.candidate_multiplier == 4
    assert config.max_candidates == brain.MAX_CANDIDATES


class RecordingConnection:
    """A connection that records statements and whether a transaction was opened."""

    def __init__(self, autocommit):
        self.autocommit = autocommit
        self.statements = []
        self.transactions = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield self


def settings_issued(connection):
    return [params[0] for sql, params in connection.statements if "set_config" in sql and params]


def test_retrieval_settings_survive_autocommit_where_set_local_would_not():
    from space import brain

    tuned = brain.RetrievalConfig(iterative_scan="relaxed_order")
    # set_config(..., true) is SET LOCAL: in autocommit the statement that sets it is
    # its own transaction, so the setting dies before the query runs and the tuning
    # silently does nothing. The settings and the query must share a transaction.
    automatic = RecordingConnection(autocommit=True)
    with brain.settings_in_force(automatic, tuned):
        pass
    assert automatic.transactions == 1
    assert settings_issued(automatic) == ["hnsw.iterative_scan"]

    # Already inside a transaction: use it rather than opening a nested one.
    explicit = RecordingConnection(autocommit=False)
    with brain.settings_in_force(explicit, tuned):
        pass
    assert explicit.transactions == 0
    assert settings_issued(explicit) == ["hnsw.iterative_scan"]

    # The default configuration changes nothing on any connection.
    for autocommit in (False, True):
        plain = RecordingConnection(autocommit=autocommit)
        with brain.settings_in_force(plain, brain.DEFAULT_RETRIEVAL):
            pass
        assert plain.transactions == 0 and settings_issued(plain) == []


def test_retrieval_configuration_rejects_nonsense_values():
    from space import brain

    with pytest.raises(ValueError):
        brain.RetrievalConfig(iterative_scan="sometimes")
    with pytest.raises(ValueError):
        brain.RetrievalConfig(expansion_order="vibes")
    with pytest.raises(ValueError):
        brain.RetrievalConfig(ef_search=0)
    with pytest.raises(ValueError):
        brain.RetrievalConfig(candidate_multiplier=0)
    # A configuration is immutable, so a strategy cannot mutate a shared default.
    with pytest.raises(FrozenInstanceError):
        brain.RetrievalConfig().ef_search = 100


def test_related_flag_never_changes_the_ranked_result():
    """Asking for related claims must not change the ranked answer.

    The related tier is declared after the ranked items take their share of the
    character budget. Declaring it first put its JSON framing into every overflow
    check, which could pop a ranked item that fitted without the flag and return
    found=false with the empty-recall note while nothing had overflowed.

    Both dimensions are swept, and the body sweep is 95 wide, because an entry that
    is too short is dropped rather than truncated, so the defect only appears when
    the response length lands within the 15 characters the flag adds. A narrower
    sweep steps over that window.
    """
    for sources in range(5, 13):
        for pad in range(0, 95):
            source = resource("Handoff", "Short fact." + "x" * pad)
            source["recall"]["sources"] = [
                {"resource_id": str(uuid4()), "revision": 3} for _ in range(sources)
            ]
            ranked = item(source, "recent_handoff")
            without = bounded_result(
                [ranked], RecallRequest(mode="resume", limit=1, max_chars=2000), url
            )
            related = bounded_result(
                [ranked],
                RecallRequest(mode="resume", limit=1, max_chars=2000, include_related=True),
                url,
            )
            related.pop("related", None)
            related.pop("related_note", None)
            assert related == without, f"sources={sources} pad={pad}: response differs"
