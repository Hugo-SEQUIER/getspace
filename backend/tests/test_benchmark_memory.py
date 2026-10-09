"""The synthetic lexical benchmark must actually read bodies and find its target."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def benchmark_module():
    path = Path(__file__).parents[1] / "scripts" / "benchmark_memory.py"
    spec = importlib.util.spec_from_file_location("benchmark_memory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("resources,expected_bodies", [(1, 1), (32, 32), (128, 32)])
def test_benchmark_reads_bounded_bodies_and_recovers_expected_source(
    benchmark_module, resources, expected_bodies
):
    result = benchmark_module.benchmark(resources)
    assert result["resources"] == resources
    assert result["indexed_bodies"] == expected_bodies
    assert result["indexed_body_bytes"] > 0
    assert result["indexed_body_bytes"] <= result["body_bytes"]
    assert result["expected_source_rank"] == 1


def test_benchmark_rejects_an_empty_retrieval(benchmark_module, monkeypatch):
    monkeypatch.setattr(benchmark_module, "rank_lexical", lambda rows, query: [])
    with pytest.raises(RuntimeError, match="relevance check failed"):
        benchmark_module.benchmark(1)


def test_benchmark_rejects_the_wrong_source(benchmark_module, monkeypatch):
    sources = benchmark_module.rows(2)
    sources[0]["body"], sources[1]["body"] = sources[1]["body"], sources[0]["body"]
    monkeypatch.setattr(benchmark_module, "rows", lambda count: sources)
    with pytest.raises(RuntimeError, match="relevance check failed"):
        benchmark_module.benchmark(2)


def test_benchmark_rejects_an_empty_fixture(benchmark_module):
    with pytest.raises(ValueError, match="at least one resource"):
        benchmark_module.benchmark(0)
