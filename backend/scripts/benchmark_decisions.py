"""Compare decision models on the decisions Space makes with them.

Parameters: --models, a comma-separated list of decision model IDs on OpenRouter, or `all`
for every text decision model OpenRouter lists; --suite relevance, classification or all;
--repeat, --limit, --concurrency and --timeout; --out for a JSON report; --list to show
the models; --env-file to read the key from another installation's settings file.
What it does: builds each case's request with the application's own code, sends it to the
Decisions API, applies the application's thresholds, and scores the decision against the
case's expected answer. The cases are invented (backend/evaluations/decisions/). The key
is SPACE_MEMORY_DECISION_API_KEY, SPACE_MEMORY_API_KEY or OPENROUTER_API_KEY, from the
environment, else from --env-file or the application's configuration; it is never printed.
Every decision costs provider usage.
Output: one table per suite and, with --out, every case's outcome as JSON.
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

from space import decision_relevance, memory_classification
from space.model_provider import DecisionError
from space.settings import DECISIONS_URL

CASES = Path(__file__).resolve().parents[1] / "evaluations" / "decisions"
CATALOG_URL = "https://openrouter.ai/api/v1/models?output_modalities=decisions"
FIELDS = ("category_ids", "project_ids", "section_ids")


KEY_NAMES = ("SPACE_MEMORY_DECISION_API_KEY", "SPACE_MEMORY_API_KEY", "OPENROUTER_API_KEY")


def configured_key(env_file=None):
    """The first key set in the environment, else in env_file, else the application's own
    configuration. Only these names are read from the file, and no value is printed."""
    sources = [os.environ]
    if env_file is not None:
        from dotenv import dotenv_values

        sources.append(dotenv_values(env_file))
    for source in sources:
        for name in KEY_NAMES:
            if (source.get(name) or "").strip():
                return source[name].strip()
    if env_file is not None:
        return None
    try:
        from space.settings import Settings

        settings = Settings()
    except Exception:
        return None
    key = settings.memory_decision_api_key or settings.memory_api_key
    return key.get_secret_value() if key else None


def decision_models(client):
    """Text decision models OpenRouter lists, without the aliases that point at another."""
    response = client.get(CATALOG_URL, timeout=30)
    response.raise_for_status()
    return [
        model
        for model in response.json()["data"]
        if "text" in model.get("architecture", {}).get("input_modalities", [])
    ]


def provider_error(response):
    """The provider's own error message, shortened. It describes the request, never a key."""
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else None
    except (AttributeError, ValueError):
        message = None
    detail = f": {' '.join(str(message).split())[:160]}" if message else ""
    return f"HTTP {response.status_code}{detail}"


def elapsed(started):
    return round((time.perf_counter() - started) * 1000, 1)


class Recorder:
    """Stands in for the model session of one case: each decision is posted, timed and
    priced. It raises DecisionError exactly where the application would fall back."""

    def __init__(self, client, url, key, model, timeout):
        self.client, self.url, self.key = client, url, key
        self.model, self.timeout = model, timeout
        self.calls = []

    def decide(self, payload):
        started, call = time.perf_counter(), {}
        self.calls.append(call)
        try:
            response = self.client.post(
                self.url,
                json={**payload, "model": self.model},
                headers={"Authorization": "Bearer " + self.key},
                timeout=self.timeout,
            )
        except httpx.HTTPError as error:
            call.update(error=type(error).__name__, latency_ms=elapsed(started))
            raise DecisionError("The decision request failed") from None
        call.update(status=response.status_code, latency_ms=elapsed(started))
        if not response.is_success:
            call["error"] = provider_error(response)
            raise DecisionError("The decision request failed")
        try:
            value = response.json()
        except ValueError:
            call["error"] = "invalid_json"
            raise DecisionError("The decision model returned invalid JSON") from None
        usage = value.get("usage") if isinstance(value, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        call.update(
            served_by=value.get("model") if isinstance(value, dict) else None,
            cost=usage.get("cost"),
            input_tokens=usage.get("input_tokens"),
        )
        return value

    def failure(self):
        """An answer that arrived but failed validation is invalid; anything else an error."""
        last = self.calls[-1] if self.calls else {}
        return "invalid" if last.get("status") == 200 and "error" not in last else "error"


def score_relevance(case, recorder):
    """Parameters: one relevance case and its recorder. Output: the outcome and details."""
    records = case["records"]
    evidence = [{"text": r["text"], "stale": r["stale"], "truncated": False} for r in records]
    expected = {
        index: case["expected"][record["id"]]
        for index, record in enumerate(records)
        if record["id"] in case["expected"]
    }
    diagnostics = {}
    labels = decision_relevance.assess(evidence, case["query"], recorder, diagnostics=diagnostics)
    if labels is None:
        reason = diagnostics.get("fallback_reason")
        outcome = recorder.failure() if reason == "provider" else "fallback"
        return {"outcome": outcome, "fallback_reason": reason}
    selected = {records[index]["id"]: label for index, label in sorted(labels.items())}
    return {"outcome": "exact" if labels == expected else "wrong", "selected": selected}


def score_classification(case, catalog, recorder):
    """Parameters: one classification case, the catalog and its recorder."""
    result, _ = memory_classification.assess(case["claim"], catalog, recorder)
    if result["reason"] == "unavailable":
        return {"outcome": recorder.failure()}
    got = {"scope": result["scope"]} | {field: sorted(result[field]) for field in FIELDS}
    want = {"scope": case["expected"]["scope"]} | {
        field: sorted(case["expected"][field]) for field in FIELDS
    }
    return {"outcome": "exact" if got == want else "wrong", "assigned": got}


def run_case(suite, case, catalog, model, client, url, key, timeout):
    recorder = Recorder(client, url, key, model, timeout)
    if suite == "relevance":
        scored = score_relevance(case, recorder)
    else:
        scored = score_classification(case, catalog, recorder)
    return {"suite": suite, "model": model, "case": case["id"], **scored, "calls": recorder.calls}


def summary(results):
    """Parameters: the results of one model on one suite. Output: its table row values."""
    counts = {
        name: sum(r["outcome"] == name for r in results)
        for name in ("exact", "wrong", "fallback", "invalid", "error")
    }
    answered = [c for r in results for c in r["calls"] if c.get("status") == 200]
    latencies = sorted(c["latency_ms"] for c in answered)
    costs = [c["cost"] for c in answered if isinstance(c.get("cost"), int | float)]
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None
    return {
        "cases": len(results),
        **counts,
        "median_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": p95,
        "served_by": sorted({c["served_by"] for c in answered if c.get("served_by")}),
        "cost_per_1000": round(sum(costs) / len(results) * 1000, 4) if costs else None,
        "errors_seen": sorted({c["error"] for r in results for c in r["calls"] if c.get("error")})[
            :3
        ],
    }


HEADER = (
    "| Model | Exact | Wrong | Fallback | Invalid | Errors | Median ms | p95 ms | $ per 1,000 |"
)


def cell(value):
    return "-" if value is None else value


def table(suite, rows):
    lines = [f"\n{suite}", HEADER, "|---|---|---|---|---|---|---|---|---|"]
    for model, row in rows.items():
        lines.append(
            f"| {model} | {row['exact']}/{row['cases']} | {row['wrong']} | {row['fallback']} "
            f"| {row['invalid']} | {row['error']} | {cell(row['median_ms'])} "
            f"| {cell(row['p95_ms'])} | {cell(row['cost_per_1000'])} |"
        )
    for model, row in rows.items():
        for message in row["errors_seen"]:
            lines.append(f"  {model}: {message}")
    return "\n".join(lines)


def load_suites(name):
    suites = {}
    if name in ("relevance", "all"):
        suites["relevance"] = (json.loads((CASES / "relevance-cases.json").read_text()), None)
    if name in ("classification", "all"):
        data = json.loads((CASES / "classification-cases.json").read_text())
        suites["classification"] = (data, data["catalog"])
    return suites


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", default="typesafe/jev-1.13")
    parser.add_argument("--suite", choices=("relevance", "classification", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--limit", type=int, help="only the first N cases of each suite")
    parser.add_argument("--env-file", type=Path, help="read the key from this settings file")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--url", default=os.environ.get("SPACE_MEMORY_DECISION_URL") or DECISIONS_URL
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list", action="store_true", help="list decision models and exit")
    args = parser.parse_args(argv)
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        if args.list:
            for model in decision_models(client):
                price = float(model.get("pricing", {}).get("prompt") or 0) * 1_000_000
                print(f"{model['id']:45s} ${price:.3f} per million input tokens")
            return 0
        key = configured_key(args.env_file)
        if not key:
            print("Set OPENROUTER_API_KEY or SPACE_MEMORY_API_KEY.", file=sys.stderr)
            return 2
        if args.models == "all":
            models = [m["id"] for m in decision_models(client) if not m["id"].startswith("~")]
        else:
            models = [m.strip() for m in args.models.split(",") if m.strip()]
        suites = load_suites(args.suite)
        jobs = [
            (suite, case, catalog, model)
            for suite, (data, catalog) in suites.items()
            for model in models
            for _ in range(args.repeat)
            for case in data["cases"][: args.limit]
        ]
        print(f"{len(jobs)} decisions across {len(models)} model(s) at {args.url}", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            results = list(
                pool.map(
                    lambda job: run_case(*job, client, args.url, key, args.timeout),
                    jobs,
                )
            )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "endpoint": args.url,
        "repeat": args.repeat,
        "suites": {
            suite: {
                "version": data["version"],
                "models": {
                    model: summary(
                        [r for r in results if r["suite"] == suite and r["model"] == model]
                    )
                    for model in models
                },
            }
            for suite, (data, _) in suites.items()
        },
        "results": results,
    }
    for suite, data in report["suites"].items():
        print(table(f"{suite} ({data['version']})", data["models"]))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
        print(f"\nReport written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
