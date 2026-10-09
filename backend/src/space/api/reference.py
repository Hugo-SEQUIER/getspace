"""The published API reference as a committed snapshot.

docs/openapi.json documents the REST API a personal API key can call; any OpenAPI
viewer renders it. This module produces that file from the running application's
schema, and a test fails when the committed snapshot no longer matches it.

Usage: python -m space.api.reference [--check]
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

SNAPSHOT = Path(__file__).resolve().parents[4] / "docs/openapi.json"


def published_schema() -> dict:
    """Parameters: none. What it does: builds the application without storage and reads
    its OpenAPI document. Output: the schema exactly as /openapi.json serves it."""
    from space.app import create_app
    from space.security.auth import TokenVerifier
    from space.settings import Settings

    settings = Settings(_env_file=None, environment="test")
    app = create_app(settings, workspace=SimpleNamespace(db=None), verifier=TokenVerifier(settings))
    return app.openapi()


def render(schema: dict) -> str:
    # Stable formatting keeps snapshot diffs readable in review.
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    rendered = render(published_schema())
    if "--check" in argv:
        if not SNAPSHOT.exists() or SNAPSHOT.read_text() != rendered:
            print(f"{SNAPSHOT} is stale. Run: python -m space.api.reference", file=sys.stderr)
            return 1
        return 0
    SNAPSHOT.write_text(rendered)
    print(f"Wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
