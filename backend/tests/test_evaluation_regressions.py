"""Audit regressions for evaluation controls and evidence, without external services."""

import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_evaluation import StubModels, outcome

from space import brain
from space.evaluation import EvaluationError, bake_embeddings, harness, percentile, product
from space.evaluation.corpus import persist_embeddings, text_key

BACKEND = Path(__file__).resolve().parents[1]


def load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli():
    return load_script(BACKEND / "scripts/evaluate_retrieval.py", "audit_retrieval_cli")


def test_cli_forwards_explicit_stale_geometry(cli, monkeypatch):
    run = Mock(return_value={})
    monkeypatch.setattr(cli, "evaluate", run)
    monkeypatch.setattr(cli, "format_table", lambda report: "measured")
    assert cli.main(["--geometry", "duplicates", "--twins", "7"]) == 0
    assert run.call_args.kwargs["geometry"] == "duplicates"
    assert run.call_args.kwargs["twins"] == 7


def test_cli_preserves_unrelated_default_and_rejects_ignored_twins(cli, capsys):
    assert cli.parse_args([]).geometry == "unrelated"
    with pytest.raises(SystemExit) as error:
        cli.parse_args(["--twins", "7"])
    assert error.value.code == 2
    assert "requires --geometry" in capsys.readouterr().err


def test_invalid_fixture_controls_fail_before_baking_or_starting_database(monkeypatch):
    bake = Mock()
    monkeypatch.setattr(harness, "bake_embeddings", bake)
    with pytest.raises(EvaluationError, match="Positive twins"):
        harness.evaluate(twins=7)
    bake.assert_not_called()


def test_product_cli_honors_rebake_and_custom_cache(cli, monkeypatch, tmp_path, capsys):
    corpus = SimpleNamespace(texts=lambda: ["claim text"])
    monkeypatch.setattr(cli, "load_corpus", lambda path: corpus)
    bake = Mock(return_value=object())
    monkeypatch.setattr(cli, "bake_embeddings", bake)
    report = json.loads((BACKEND / "evaluations/retrieval/report-isolation.json").read_text())
    monkeypatch.setattr(cli, "run_product_evaluation", lambda *args, **kwargs: report)
    cache = tmp_path / "custom.json"
    assert cli.main(["--product-path", "--rebake", "--embeddings", str(cache)]) == 0
    bake.assert_called_once_with(["claim text"], path=str(cache), rebake=True)
    output = capsys.readouterr().out
    for combination in report["combinations"]:
        assert combination["expansion"] in output


def test_extended_embedding_cache_is_saved_back_to_its_original_path(tmp_path):
    cache = tmp_path / "custom.json"
    embeddings = bake_embeddings(["first"], path=cache, models=StubModels())
    embeddings.vectors[text_key("new cue")] = embeddings.vector("first")
    persist_embeddings(embeddings)
    assert text_key("new cue") in json.loads(cache.read_text())["vectors"]


def test_embedding_calls_measure_the_provider_session_instead_of_assuming_batch_size(tmp_path):
    models = StubModels()
    embeddings = bake_embeddings(
        [f"text {index}" for index in range(33)], path=tmp_path / "cache.json", models=models
    )
    assert embeddings.calls == models.calls == 1


def test_product_sweep_counts_only_calls_made_by_each_threshold(monkeypatch):
    models = SimpleNamespace(provider_calls=7)
    fixture = SimpleNamespace(
        models=models,
        service=SimpleNamespace(retrieval=brain.RetrievalConfig()),
        claim_map={"claim": "c001"},
        info=lambda: {"provider_calls": models.provider_calls},
    )
    selected = [SimpleNamespace(query="query", kind="keyword", relevant=("c001",))]
    monkeypatch.setattr(
        product, "build_product_fixture", lambda *args, **kwargs: (fixture, selected)
    )
    monkeypatch.setattr(product, "product_config", lambda **kwargs: nullcontext())
    monkeypatch.setattr(product, "relevant_claim_similarity", lambda *args: {})
    calls = iter([2, 0, 1, 0])

    def run_query(*args, **kwargs):
        models.provider_calls += next(calls)
        return outcome()

    monkeypatch.setattr(product, "run_product_query", run_query)
    report = product.run_product_evaluation(
        SimpleNamespace(path=Path("corpus.json")),
        object(),
        database=object(),
        floors=[0.0, 0.2],
        modes=["both_on", "both_off"],
    )
    assert [
        threshold["metrics"]["embed_calls"]
        for combination in report["combinations"]
        for threshold in combination["thresholds"]
    ] == [2, 0, 1, 0]
    assert report["fixture"]["provider_calls"] == 10


def test_fractional_percentile_uses_the_ceiling_of_the_full_rank():
    assert percentile([1.0, 2.0, 3.0], 33.4) == 2.0
