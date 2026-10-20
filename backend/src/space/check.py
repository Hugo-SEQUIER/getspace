"""Check that this installation's settings work, and say plainly what to fix.

Parameters: the API's environment settings (see the repository's .env.example);
--offline skips every check that calls a model provider or the public address.
What it does: validates the settings, connects to the database as the API does, then asks
the text model, the embedding model, the Anthropic model when its key is set and the
decision model one tiny synthetic question each with the application's own code, compares
the embedding size with the memory index, and fetches the public address. Secrets and
provider replies are never printed.
Output: one line per check, PASS, WARN or FAIL, and exit status 1 when anything failed.
Run it in a Compose installation with: docker compose exec api python -m space.check
"""

import argparse
import time
from urllib.parse import urlsplit

import httpx
import psycopg
from pydantic import BaseModel, ValidationError

from space import brain, decision_relevance
from space.model_provider import MemoryModels, ModelError
from space.settings import CONFIG_FILE, Settings, database_connection_options, private_host

# Provider statuses in words; the error text itself never carries a provider reply.
HINTS = (
    ("HTTP 401", "the API key was refused"),
    ("HTTP 402", "the provider account has no credit left"),
    ("HTTP 403", "this API key may not use this model"),
    ("HTTP 404", "the model or the endpoint was not found"),
    ("HTTP 429", "the provider is rate limiting this key, or it has no credit left"),
)


class Answer(BaseModel):
    ok: bool


class Report:
    def __init__(self, write=print):
        self.write, self.failures, self.warnings = write, 0, 0

    def add(self, status, subject, detail):
        self.failures += status == "FAIL"
        self.warnings += status == "WARN"
        self.write(f"{status:4}  {subject}: {detail}")


def hint(error) -> str:
    text = str(error)
    for code, words in HINTS:
        if code in text:
            return f"{words} ({code})"
    if "budget" in text or "timeout" in text.lower():
        return "no answer in time"
    return text.rstrip(".")


def milliseconds(started) -> int:
    return round((time.perf_counter() - started) * 1000)


def load(report) -> Settings | None:
    try:
        settings = Settings(_env_file=CONFIG_FILE)
    except ValidationError as error:
        for problem in error.errors():
            field = ".".join(str(part) for part in problem["loc"]).upper()
            message = problem["msg"].removeprefix("Value error, ")
            report.add("FAIL", "Settings", f"SPACE_{field}: {message}" if field else message)
        return None
    report.add("PASS", "Settings", "loaded and valid")
    return settings


def access(report, settings):
    host = urlsplit(settings.web_url).hostname
    if settings.access_mode == "key":
        report.add("PASS", "Access", "key mode: the website asks for the access key")
    elif private_host(host):
        report.add("PASS", "Access", f"private mode: no checks, so keep {host} private")
    else:
        report.add(
            "FAIL",
            "Access",
            f"private mode on the public address {host}: anyone who reaches it can read and "
            "change everything. Use key mode, or a private address",
        )


def address(report, settings):
    host = urlsplit(settings.web_url).hostname or ""
    if host not in settings.allowed_hosts:
        report.add(
            "FAIL",
            "Address",
            f"SPACE_ALLOWED_HOSTS does not include {host}, so the API refuses that address",
        )
    else:
        report.add("PASS", "Address", f"{settings.web_url}, allowed by SPACE_ALLOWED_HOSTS")
    expected = settings.web_url.rstrip("/") + "/mcp/"
    if settings.mcp_resource_url != expected:
        report.add(
            "WARN",
            "MCP address",
            f"agents connect at {settings.mcp_resource_url}, not at {expected} on the "
            "public address",
        )
    if settings.access_mode == "key" and urlsplit(settings.web_url).scheme == "https":
        report.add("PASS", "App sign-in", "apps such as claude.ai can connect by signing in")
    else:
        report.add(
            "WARN",
            "App sign-in",
            "apps such as claude.ai need key mode on a public HTTPS address; agents with a "
            "token or on this network still connect",
        )


def database(report, settings) -> int | None:
    """Connect as the API does. Output: the memory index's embedding size, or None."""
    if settings.database_url is None:
        report.add("FAIL", "Database", "SPACE_DATABASE_URL is not set")
        return None
    try:
        with psycopg.connect(
            settings.database_url.get_secret_value(),
            **database_connection_options(settings),
            connect_timeout=8,
        ) as connection:
            user = connection.execute("SELECT current_user").fetchone()[0]
            width = brain.load_dimensions(connection)
    except psycopg.Error:
        report.add(
            "FAIL",
            "Database",
            "cannot connect; check SPACE_DATABASE_URL and that the database is running",
        )
        return None
    if user != "space_app":
        report.add("FAIL", "Database", f"connected as {user}; the API must use space_app")
    else:
        report.add("PASS", "Database", f"connected as space_app; memory index of {width} numbers")
    return width


def public(report, settings, client):
    url = urlsplit(settings.web_url)
    if url.scheme != "https" or private_host(url.hostname):
        return
    try:
        response = client.get(settings.web_url.rstrip("/") + "/health/live", timeout=8)
    except httpx.ConnectError as error:
        detail = (
            "the HTTPS certificate is not valid yet; see `docker compose logs caddy`"
            if "CERTIFICATE" in str(error).upper()
            else "cannot connect; check the DNS record and that ports 80 and 443 are open"
        )
        report.add("WARN", "Public address", detail)
        return
    except httpx.HTTPError:
        report.add("WARN", "Public address", f"{url.hostname} did not answer in time")
        return
    if response.status_code == 200:
        report.add("PASS", "Public address", f"{url.hostname} answers over HTTPS")
    else:
        report.add("WARN", "Public address", f"{url.hostname} answered HTTP {response.status_code}")


def models(report, settings, width):
    if settings.memory_provider == "disabled":
        report.add(
            "PASS",
            "Memory provider",
            "off: documents, tasks and word search work; automatic memory needs a provider",
        )
        return
    # Without the Anthropic key, which would otherwise answer first, so each line names the
    # model that answered; the Anthropic route gets its own check below.
    provider = settings.model_copy(update={"memory_anthropic_api_key": None})
    session = MemoryModels(provider).session(lambda: None)
    question = "Set ok to true."
    for purpose, name in (
        ("memory", settings.memory_text_model),
        ("relevance", settings.memory_relevance_model),
    ):
        if purpose == "relevance" and name in (None, settings.memory_text_model):
            continue
        started = time.perf_counter()
        try:
            session.invoke(question, response_format=Answer, purpose=purpose)
            report.add(
                "PASS",
                "Text model" if purpose == "memory" else "Relevance model",
                f"{name} answered in {milliseconds(started)} ms",
            )
        except ModelError as error:
            report.add(
                "FAIL",
                "Text model" if purpose == "memory" else "Relevance model",
                f"{name}: {hint(error)}",
            )
    name = settings.memory_embedding_model
    started = time.perf_counter()
    try:
        size = len(session.embed(["Space settings check"])[0])
    except ModelError as error:
        report.add("FAIL", "Embedding model", f"{name}: {hint(error)}")
    else:
        if width is not None and size != width:
            report.add(
                "FAIL",
                "Embedding model",
                f"{name} returns {size} numbers but the memory index stores {width}. Set "
                f"SPACE_MEMORY_EMBEDDING_DIMENSIONS={size} while no memory is stored, then "
                "restart",
            )
        else:
            report.add("PASS", "Embedding model", f"{name} answered in {milliseconds(started)} ms")
    if settings.memory_anthropic_api_key:
        anthropic(report, settings, question)
    if settings.uses_decisions:
        decision(report, settings, session)


def anthropic(report, settings, question):
    """Ask the Anthropic route alone: invoke would fall back to the provider and hide it."""
    session = MemoryModels(settings).session(lambda: None)
    name = settings.memory_anthropic_model
    started = time.perf_counter()
    try:
        schema = Answer.model_json_schema()
        session._parse(
            session._anthropic_chat("Return only JSON.", question, schema, "memory", None), Answer
        )
    except ModelError as error:
        report.add("FAIL", "Anthropic model", f"{name}: {hint(error)}")
        return
    report.add("PASS", "Anthropic model", f"{name} answered in {milliseconds(started)} ms")


def decision(report, settings, session):
    payload = {
        "state": "Space settings check: the sky is blue.",
        "questions": {
            "blue": {
                "type": "noul",
                "instructions": "Does the text say that the sky is blue?",
                "criteria": {"true": "It says the sky is blue.", "false": "It does not."},
            }
        },
    }
    name = settings.memory_decision_model
    started = time.perf_counter()
    try:
        response = session.decide(payload)
        decision_relevance.validate_response(payload, response)
    except ModelError as error:
        report.add("FAIL", "Decision model", f"{name}: {hint(error)}")
        return
    served = response.get("model")
    served = f" (served as {served})" if served and served != name else ""
    report.add("PASS", "Decision model", f"{name} answered in {milliseconds(started)} ms{served}")


def run(offline=False, write=print) -> int:
    report = Report(write)
    write("Space settings check")
    settings = load(report)
    if settings is not None:
        access(report, settings)
        address(report, settings)
        width = database(report, settings)
        if offline:
            report.add("WARN", "Models", "not called (--offline)")
        else:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                public(report, settings, client)
            models(report, settings, width)
    if report.failures:
        write(f"\n{report.failures} problem(s) to fix.")
    else:
        write(
            "\nEverything checked works."
            if not report.warnings
            else "\nNo problems; see the warnings."
        )
    return 1 if report.failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="do not call models or the address")
    return run(parser.parse_args(argv).offline)


if __name__ == "__main__":
    raise SystemExit(main())
