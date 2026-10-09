from uuid import uuid4

import pytest

from space import archive_schema, memory_pipeline
from space.memory import RememberMemory


@pytest.mark.parametrize("length", [1, 2000, 2001, 12000])
def test_authored_text_is_lossless_bounded_and_has_no_generated_cues(length):
    text = (" Original wording.\n" * 1000)[:length]
    if not text.strip():
        text = "x"
    request = RememberMemory(
        command_id=uuid4(), title="Title || " + "x" * 290, text=text, importance=3
    )
    resource_id = uuid4()
    record = memory_pipeline.authored(request, resource_id, "fixture", "2026-09-21T00:00:00Z")
    assert "".join(claim["value"] for claim in record["claims"]) == text
    assert len(record["claims"]) == (length + 1999) // 2000
    assert all(0 < len(c["value"]) <= 2000 and len(c["index"]) <= 300 for c in record["claims"])
    assert all("||" not in c["index"] and c["importance"] == 3 for c in record["claims"])
    assert len({claim["id"] for claim in record["claims"]}) == len(record["claims"])
    assert all(row["cues"] == [] for row in memory_pipeline.index_rows(record))
    assert record == memory_pipeline.authored(
        request, resource_id, "fixture", "2026-09-21T00:00:00Z"
    )
    archive_schema._memory(record)
