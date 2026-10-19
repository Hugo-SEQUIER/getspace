"""The decision-model benchmark, against a fake Decisions API: no live calls, no key leaks."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_decisions.py"
KEY = "sk-benchmark-canary"


@pytest.fixture
def bench(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("benchmark_decisions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    relevance = {
        "version": "test-relevance",
        "cases": [
            {
                "id": "answered",
                "query": "Where are Cedar records stored?",
                "records": [
                    {"id": "r1", "text": "Cedar records are stored in PostgreSQL.", "stale": False},
                    {"id": "r2", "text": "Juniper uses SQLite.", "stale": False},
                ],
                "expected": {"r1": "direct"},
            },
            {
                "id": "abstain",
                "query": "Who owns the Foal service?",
                "records": [{"id": "r1", "text": "Kerin owns the Foals service.", "stale": False}],
                "expected": {},
            },
        ],
    }
    catalog = [
        {
            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "kind": "category",
            "name": "Dev",
            "description": "Software.",
        },
        {
            "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "kind": "category",
            "name": "Food",
            "description": "Cooking.",
        },
        {
            "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "kind": "section",
            "name": "Work",
            "description": "Work projects.",
        },
        {
            "id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "kind": "project",
            "name": "Atlas",
            "description": "Billing app.",
            "section_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        },
    ]
    classification = {
        "version": "test-classification",
        "catalog": catalog,
        "cases": [
            {
                "id": "atlas",
                "claim": {"index": "atlas tests", "value": "Atlas needs pytest."},
                "expected": {
                    "scope": "project",
                    "category_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                    "project_ids": ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
                    "section_ids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
                },
            }
        ],
    }
    (tmp_path / "relevance-cases.json").write_text(json.dumps(relevance))
    (tmp_path / "classification-cases.json").write_text(json.dumps(classification))
    monkeypatch.setattr(module, "CASES", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    for name in ("SPACE_MEMORY_DECISION_API_KEY", "SPACE_MEMORY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return module


def answers(payload):
    """A careful fake model: selects the PostgreSQL record, matches Dev and Atlas."""
    result = {}
    candidates = payload["state"].get("candidates", [])
    for key, question in payload["questions"].items():
        if question["type"] == "noul":
            target = (
                question["instructions"].get("target", {})
                if isinstance(question["instructions"], dict)
                else {}
            )
            yes = (
                key == "answerable" and any("PostgreSQL" in c["text"] for c in candidates)
            ) or target.get("name") in ("Dev", "Atlas")
            result[key] = {"type": "noul", "noul": 0.95 if yes else 0.05}
            continue
        if key == "temporal":
            choice = "unspecified"
        else:
            text = candidates[int(key[1:])]["text"]
            choice = "direct" if "PostgreSQL" in text else "irrelevant"
        options = list(question["criteria"])
        rest = 0.05 / (len(options) - 1)
        result[key] = {
            "type": "choice",
            "choice": choice,
            "probabilities": {o: 0.95 if o == choice else rest for o in options},
        }
    return result


def transport(requests):
    def handler(request):
        body = json.loads(request.content)
        requests.append((request.headers.get("authorization"), body["model"]))
        if body["model"] == "broken/model":
            return httpx.Response(500, json={"error": {"message": "down"}})
        if body["model"] == "sloppy/model":
            return httpx.Response(200, json={"model": body["model"], "answers": {}, "usage": {}})
        return httpx.Response(
            200,
            json={
                "model": body["model"] + "-20261001",
                "answers": answers(body),
                "usage": {"input_tokens": 100, "output_tokens": 1, "cost": 0.00001},
            },
        )

    return httpx.MockTransport(handler)


def test_models_are_scored_on_the_applications_own_decisions(bench, monkeypatch, tmp_path, capsys):
    requests = []
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=transport(requests), **kw)
    )
    out = tmp_path / "report.json"
    code = bench.main(
        [
            "--models",
            "good/model,sloppy/model,broken/model",
            "--out",
            str(out),
            "--concurrency",
            "2",
        ]
    )
    assert code == 0
    report = json.loads(out.read_text())
    relevance = report["suites"]["relevance"]["models"]
    assert relevance["good/model"]["exact"] == 2 and relevance["good/model"]["cases"] == 2
    assert relevance["good/model"]["served_by"] == ["good/model-20261001"]
    assert relevance["good/model"]["cost_per_1000"] == 0.01
    assert relevance["sloppy/model"]["invalid"] == 2
    assert relevance["broken/model"]["error"] == 2
    classification = report["suites"]["classification"]["models"]
    assert classification["good/model"]["exact"] == 1
    assert classification["sloppy/model"]["invalid"] == 1
    assert classification["broken/model"]["error"] == 1
    # Every request carried the configured key, and the key appears nowhere else.
    assert {auth for auth, _ in requests} == {"Bearer " + KEY}
    printed = capsys.readouterr()
    assert KEY not in out.read_text() and KEY not in printed.out + printed.err
    assert "| good/model | 2/2 |" in printed.out


def test_a_missing_key_stops_before_any_request(bench, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(bench, "configured_key", lambda env_file=None: None)
    assert bench.main(["--models", "good/model"]) == 2


def test_the_key_can_come_from_another_installations_settings_file(bench, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    settings = tmp_path / "other.env"
    settings.write_text(
        "SPACE_MEMORY_RELEVANCE_PROVIDER=typesafe\nSPACE_MEMORY_API_KEY='" + KEY + "'\n"
    )
    assert bench.configured_key(settings) == KEY
    assert bench.configured_key(tmp_path / "missing.env") is None


def test_provider_errors_are_named_and_cases_can_be_limited(bench, monkeypatch, capsys):
    real_client = httpx.Client

    def refuse(request):
        return httpx.Response(400, json={"error": {"code": 400, "message": "Unknown model"}})

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw)
    )
    assert bench.main(["--models", "missing/model", "--limit", "1", "--suite", "relevance"]) == 0
    printed = capsys.readouterr().out
    assert "| missing/model | 0/1 | 0 | 0 | 0 | 1 |" in printed
    assert "missing/model: HTTP 400: Unknown model" in printed
