"""Session continuity: long progress must not hide the next action or blocker."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from space.briefing import compact_handoff
from space.memory import RecallRequest, SaveHandoff, bounded_result


def long_handoff():
    return SaveHandoff(
        command_id=uuid4(),
        title="Larch acceptance",
        summary="Larch preview moved to October 4.",
        decisions=["Keep offline operation and original invoice currencies."],
        progress=["Browser styling checked. " * 55] * 8,
        unresolved_questions=["Zero-value invoice acceptance is still unverified."],
        next_actions=["Ask Mira to verify zero-value invoice upload and export."],
    )


def test_compact_resume_retains_action_blocker_constraint_and_source_under_budget():
    handoff = long_handoff()
    source = {
        "id": str(uuid4()),
        "revision": 3,
        "kind": "document",
        "title": handoff.title,
        "status": "todo",
        "updated_at": "2026-09-15T00:00:00Z",
        "author": "agent",
        "category": "handoff",
        "reason": "recent_handoff",
        "body": handoff.body(),
        "verification": "handoff",
        "stale": True,
        "sources": [{"resource_id": str(uuid4()), "revision": 2}],
    }
    original = source["body"]

    def url(id_, revision):
        return f"https://space.test/{id_}/{revision}"

    normal = bounded_result([source], RecallRequest(mode="resume"), url)
    assert "Ask Mira" not in normal["items"][0]["excerpt"]
    request = RecallRequest(mode="resume", compact=True, max_chars=2000)
    result = bounded_result([source], request, url)
    item = result["items"][0]
    for phrase in ("Ask Mira", "still unverified", "offline operation", "October 4"):
        assert phrase in item["excerpt"]
    assert len(json.dumps(result, ensure_ascii=False)) <= request.max_chars
    assert item["truncated"] and item["stale"]
    assert item["revision"] == 3 and item["verification"] == "handoff"
    assert item["sources"][0]["revision"] == 2
    assert source["body"] == original


@pytest.mark.parametrize("maximum", [120, 400, 1200])
def test_unicode_and_escaped_text_respect_the_excerpt_budget(maximum):
    note = long_handoff().model_copy(update={"summary": '한국어 "요약" \\ ' * 80})
    result, truncated = compact_handoff(note.body(), maximum)
    assert len(result) <= maximum and truncated
    assert "Next actions" in result


def test_short_and_manually_edited_handoffs_preserve_their_original_layout():
    note = long_handoff().model_copy(update={"progress": ["Upload tested."]})
    assert compact_handoff(note.body()) == (note.body(), False)
    for body in ("Manually corrected. " * 200, "## Next actions\nManually edited. " * 200):
        assert compact_handoff(body) == (body[:1200], True)
    # Ambiguous section headings in a user's note must not silently overwrite content.
    body = note.body() + "\n\n## Decisions\n" + "Second section. " * 200
    assert compact_handoff(body) == (body[:1200], True)


def test_compact_is_opt_in_and_only_supported_for_resume():
    assert not RecallRequest(mode="resume").compact
    with pytest.raises(ValidationError, match="resume mode"):
        RecallRequest(query="Larch", compact=True)
    with pytest.raises(ValidationError):
        RecallRequest(mode="resume", compact="true")
