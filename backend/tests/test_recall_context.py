"""Parameters: disposable database fixtures and authored evidence.
What it does: checks the provenance and privacy of bounded recall context.
Output: deterministic regression evidence, without provider or live data calls.
"""

from uuid import UUID

import pytest
from test_database_setup import database as database_fixture
from test_memora_workspace import setup
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import RecallMetadata, RecallRequest

command_database = command_database_fixture
commands = commands_fixture
database = database_fixture


@pytest.mark.parametrize("excluded", [False, True])
def test_source_context_uses_cited_title_but_honors_current_exclusion(commands, excluded):
    service, owner, space, source, build = setup(commands)
    saved = service.build_memory(owner, space, build)
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(source["id"]),
                "expected_revision": 1,
                "title": "RENAMED CURRENT TITLE",
                "body": "Mira travels by car.",
                "recall": RecallMetadata(excluded=excluded),
            }
        ),
    )
    calls = len(service.memory_models.calls)
    result = service.context(owner, space, RecallRequest(query="car", strategy="lexical"))
    assert len(service.memory_models.calls) == calls
    memory = next(item for item in result["items"] if item["id"] == saved["id"])
    context = memory["evidence_context"]
    assert context["resource_title"] == saved["title"]
    assert memory["sources"][0]["revision"] == 1
    assert memory["stale"] is True
    if excluded:
        assert "source_titles" not in context
        assert context["incomplete"] is True
    else:
        assert context["source_titles"] == ["Commute source"]
        assert "RENAMED CURRENT TITLE" not in str(context)
