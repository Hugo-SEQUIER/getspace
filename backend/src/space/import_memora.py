"""Preview or import a Memora JSON export through Space's authenticated REST API.

Run `python -m space.import_memora --help`. Preview is offline and is the default.
Only --apply enables writes. Credentials come from SPACE_IMPORT_TOKEN, never argv.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import ValidationError

from space.memora_import import ImportProblem, Plan, plan_import, read_export
from space.memory import SourceReference
from space.settings import private_host


def api_url(value):
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ImportProblem("Invalid Space API URL") from None
    if (
        not url.host
        or (url.scheme != "https" and not (url.scheme == "http" and private_host(url.host)))
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise ImportProblem(
            "API URL must use HTTPS (or HTTP on a private network), without credentials or query"
        )
    return str(url).rstrip("/") + "/"


class SpaceClient:
    """Normal authenticated API calls; no provider or database credentials are loaded."""

    def __init__(self, url, token, *, transport=None):
        if not token or len(token) > 16384 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ImportProblem("Set SPACE_IMPORT_TOKEN to the access key or a read-write API key")
        self.client = httpx.Client(
            base_url=api_url(url),
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(210, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, method, space_id, suffix, payload=None):
        # Do not echo response bodies: validation errors or proxies may include
        # private text, tokens or raw upstream failures. Resume with the same IDs.
        try:
            response = self.client.request(
                method,
                f"v1/spaces/{space_id}/{suffix}",
                params={"workspace_id": str(space_id)},
                json=payload,
            )
        except httpx.HTTPError:
            raise ImportProblem(
                "Space API request failed; rerun the same import to resume"
            ) from None
        if not response.is_success:
            guidance = {
                401: "Refresh the browser access token for the same account and retry.",
                403: "Check write access to every mapped Space.",
                404: "Check the API URL, destination Spaces and server version.",
                409: "A command or source conflicts; keep the source ID and inspect the import.",
                429: "Space is rate limited or at quota; resolve the limit before retrying.",
                503: "Check the memory provider and server capacity, then retry.",
            }.get(response.status_code, "Check the server configuration and retry the same import.")
            raise ImportProblem(f"Space API returned HTTP {response.status_code}. {guidance}")
        try:
            result = response.json()
        except ValueError:
            raise ImportProblem("Space API returned invalid JSON; retry the same import") from None
        if not isinstance(result, dict):
            raise ImportProblem("Space API returned an unexpected response; retry the same import")
        return result


def _source_reference(response):
    try:
        return SourceReference(resource_id=response["id"], revision=response["revision"])
    except (KeyError, ValidationError):
        raise ImportProblem("Space API did not return a valid resource ID and revision") from None


def apply_plan(plan: Plan, client: SpaceClient, *, progress=None):
    """Sequential, restartable writes. Completed records are not rolled back on failure."""
    if not plan.ready:
        raise ImportProblem("Resolve all record errors and namespace mappings before applying")
    # Check every target before creating any memories. Authorization for
    # writes remains enforced by the server on each normal command.
    for target in sorted(set(plan.destinations.values()), key=str):
        provider = client.request("GET", target, "memory/provider")
        if provider.get("enabled") is not True:
            raise ImportProblem("Enable a memory provider in every destination Space before import")
    completed = 0
    for item in plan.writes:
        try:
            saved_memory = client.request(
                "POST", item.space_id, "memory/claims", item.memory().model_dump(mode="json")
            )
            _source_reference(saved_memory)
        except ImportProblem as exc:
            raise ImportProblem(
                f"Stopped at export row {item.record.row} after {completed} completed memories. "
                f"{exc} A memory may already have committed; rerun with the same "
                "source ID, mappings, export and account."
            ) from None
        completed += 1
        if progress:
            progress(completed, len(plan.writes))
    return {
        "status": "complete",
        "memories_processed": completed,
        "source_documents_processed": 0,
    }


def _mapping(values):
    result = {}
    for value in values:
        namespace, separator, target = value.rpartition("=")
        if not separator or not namespace or namespace in result:
            raise ImportProblem("Use each --map NAMESPACE=SPACE_UUID exactly once")
        try:
            result[namespace] = UUID(target)
        except ValueError:
            raise ImportProblem("Each --map destination must be a Space UUID") from None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "export", type=Path, help="full-export/memories JSON file or export directory"
    )
    parser.add_argument(
        "--source-id", required=True, help="stable name for the original Memora store"
    )
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument("--map", action="append", default=[], metavar="NAMESPACE=SPACE_UUID")
    destinations.add_argument(
        "--space", type=UUID, help="explicitly combine all namespaces in one Space"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="write through the API; default is preview"
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="explicit offline preview (the default)"
    )
    parser.add_argument("--api-url", help="Space origin, e.g. https://space.example.com (no /v1)")
    args = parser.parse_args(argv)
    client = None
    try:
        plan = plan_import(
            read_export(args.export), args.source_id, _mapping(args.map), space_id=args.space
        )
        print(json.dumps(plan.summary(), ensure_ascii=False, indent=2), flush=True)
        if not args.apply:
            return 1 if plan.errors else 0
        if not plan.ready:
            raise ImportProblem("Resolve all record errors and namespace mappings before applying")
        if not args.api_url:
            raise ImportProblem("--apply requires --api-url")
        client = SpaceClient(args.api_url, os.environ.get("SPACE_IMPORT_TOKEN", ""))

        def progress(done, total):
            print(
                f"Processed {done}/{total} memories (saved or reused).", file=sys.stderr, flush=True
            )

        print(json.dumps(apply_plan(plan, client, progress=progress)), flush=True)
        return 0
    except ImportProblem as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Rerun with the same source ID, mappings, export and account.",
            file=sys.stderr,
        )
        return 130
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
