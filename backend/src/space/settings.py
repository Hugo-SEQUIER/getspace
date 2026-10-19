"""Validated application configuration, with secret values wrapped for safe display."""

import ipaddress
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_FILE = BACKEND_DIR / ".env"
PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".ts.net")


def private_host(hostname: str | None) -> bool:
    """True for loopback and private-network addresses, compose service names (one label,
    such as `ollama` or `api`) and private DNS suffixes. Plain HTTP is accepted only there."""
    if not hostname:
        return False
    name = hostname.strip("[]").lower().rstrip(".")
    try:
        return not ipaddress.ip_address(name).is_global
    except ValueError:
        pass
    return name == "localhost" or "." not in name or name.endswith(PRIVATE_SUFFIXES)


def _plain_url(value: str) -> bool:
    url = urlsplit(value)
    return (
        url.scheme in {"http", "https"}
        and bool(url.hostname)
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and (url.scheme == "https" or private_host(url.hostname))
    )


# OpenRouter's Decisions API, which serves decision models such as TypeSafe's Jev.
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPACE_", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["development", "test", "production"] = "development"
    # key: the website asks for the access key once per browser, and agents connect with
    # their own revocable tokens. private: no access check at all; keep the instance on
    # localhost, an SSH tunnel or a VPN.
    access_mode: Literal["key", "private"] = "key"
    access_key: SecretStr | None = Field(default=None, min_length=32, max_length=512)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])
    # Browser origins allowed to call the API directly with an API key. Requests carry
    # no cookies, so "*" is safe; an empty list turns cross-origin access off.
    api_cors_origins: list[str] = Field(default_factory=lambda: ["*"], max_length=50)
    database_url: SecretStr | None = None
    job_database_url: SecretStr | None = None
    webhook_database_url: SecretStr | None = None
    # A compose network reaches PostgreSQL privately, so TLS is optional by default.
    # Use verify-full with SPACE_DATABASE_CA_FILE for a database across the internet.
    database_sslmode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"
    database_ca_file: Path | None = None
    # Bearer token for /ops/metrics. Unset hides the endpoint.
    ops_token: SecretStr | None = Field(default=None, min_length=32, max_length=256)
    web_url: str = "http://127.0.0.1:3000"
    memory_provider: Literal["disabled", "ollama", "openai_compatible"] = "disabled"
    memory_provider_url: str | None = None
    memory_api_key: SecretStr | None = None
    memory_text_model: str | None = None
    memory_relevance_model: str | None = None
    # Decision models answer typed questions with probabilities instead of text. They
    # judge recall evidence, organize memories and link related ones, each when its
    # provider is "decision"; "typesafe", the earlier name, still means the same. Any
    # decision model on the endpoint can replace Jev (scripts/benchmark_decisions.py).
    memory_relevance_provider: Literal["model", "decision", "typesafe"] = "model"
    memory_classification_provider: Literal["disabled", "decision", "typesafe"] = "disabled"
    memory_relationship_provider: Literal["disabled", "decision", "typesafe"] = "disabled"
    memory_decision_model: str = "perplexity/pplx-decider-v1.1-27b"
    memory_decision_url: str = DECISIONS_URL
    # Unset uses SPACE_MEMORY_API_KEY, as when OpenRouter serves both text and decisions.
    memory_decision_api_key: SecretStr | None = None
    # A decision that takes longer falls back like any other decision failure.
    memory_decision_timeout: float = Field(default=5, ge=1, le=60)
    memory_embedding_model: str | None = None
    # Embeddings may live on a different endpoint and key from the chat model, because
    # a provider can serve chat completions without an embedding route. Unset keeps
    # both calls on memory_provider_url, which is the single-provider default.
    memory_embedding_url: str | None = None
    memory_embedding_api_key: SecretStr | None = None
    # Only for a model that accepts reasoning_effort. A model that takes temperature
    # instead leaves both unset, and each request sends its caller's temperature.
    memory_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    memory_relevance_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    mcp_resource_url: str = Field(
        default_factory=lambda: f"http://127.0.0.1:{os.getenv('BACKEND_PORT', '8000')}/mcp/"
    )
    # Per-process bounds, not a measured service-capacity guarantee. Separate lanes
    # keep model-backed writes out of recall slots; waiting consumes no API worker.
    recall_concurrency: int = Field(default=12, ge=1, le=256)
    recall_queue_size: int = Field(default=128, ge=0, le=4096)
    recall_queue_timeout: float = Field(default=5, ge=0, le=60)
    memory_write_concurrency: int = Field(default=2, ge=1, le=256)
    memory_write_queue_size: int = Field(default=32, ge=0, le=4096)
    memory_write_queue_timeout: float = Field(default=5, ge=0, le=60)
    database_pool_size: int = Field(default=4, ge=1, le=256)
    database_api_processes: int = Field(
        default_factory=lambda: int(os.getenv("BACKEND_WORKERS", "1")), ge=1, le=256
    )
    database_worker_processes: int = Field(default=1, ge=0, le=256)
    # Each webhook delivery process also holds one application connection, for
    # owner authorization and reading the subscription, beside its separate queue login.
    database_webhook_processes: int = Field(default=0, ge=0, le=256)
    database_additional_connections: int = Field(default=0, ge=0, le=256)
    database_replicas: int = Field(default=1, ge=1, le=100)
    database_deployment_overlap: int = Field(default=1, ge=1, le=4)

    @property
    def database_connection_budget(self):
        return (
            (
                self.database_pool_size * self.database_api_processes
                + self.database_worker_processes
                + self.database_webhook_processes
                + self.database_additional_connections
            )
            * self.database_replicas
            * self.database_deployment_overlap
        )

    # Exact per-Space ranking is the serving default. HNSW controls are retained for
    # compatibility but do not change the exact query's candidates. The optional
    # floor is only a geometric cutoff; evidence relevance is assessed separately.
    memory_ef_search: int | None = Field(default=None, ge=1, le=10000)
    memory_iterative_scan: Literal["off", "relaxed_order", "strict_order"] = "off"
    memory_score_floor: float | None = Field(default=None, ge=-1.0, le=1.0)
    memory_expansion: bool = True
    # Whether an agent should mine saved documents into memory on its own initiative.
    # Off by default: a document is already findable by the words it stores, and mining
    # it adds claim rows and consumes part of the resource budget. The served guide
    # states this policy, so agent behaviour cannot drift from the setting.
    mine_documents: bool = False

    @property
    def uses_decisions(self) -> bool:
        """Whether any memory feature asks the decision model."""
        return "decision" in (
            self.memory_relevance_provider,
            self.memory_classification_provider,
            self.memory_relationship_provider,
        )

    @field_validator(
        "memory_relevance_provider",
        "memory_classification_provider",
        "memory_relationship_provider",
    )
    @classmethod
    def decision_provider(cls, value):
        # Installations configured before decision models went through OpenRouter.
        return "decision" if value == "typesafe" else value

    @field_validator("*", mode="before")
    @classmethod
    def blank_as_default(cls, value, info):
        # An empty value means unset, like an absent one: compose passes every optional
        # setting through, empty when .env leaves it blank.
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].get_default(call_default_factory=True)
        return value

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.access_mode == "key" and self.database_url and not self.access_key:
            raise ValueError("Key access requires SPACE_ACCESS_KEY of at least 32 characters")
        if self.uses_decisions:
            key = self.memory_decision_api_key or self.memory_api_key
            endpoint = urlsplit(self.memory_decision_url)
            local = private_host(endpoint.hostname)
            if (
                self.memory_provider == "disabled"
                or not self.memory_decision_model.strip()
                or not (endpoint.scheme == "https" or (local and endpoint.scheme == "http"))
                or not (local or (key and key.get_secret_value().strip()))
            ):
                raise ValueError(
                    "Decision features require a memory provider, a decision model, an HTTPS "
                    "decision endpoint and SPACE_MEMORY_DECISION_API_KEY or SPACE_MEMORY_API_KEY"
                )
        if self.memory_provider != "disabled":
            if not all(
                (self.memory_provider_url, self.memory_text_model, self.memory_embedding_model)
            ):
                raise ValueError(
                    "Memory requires an explicit endpoint, text model and embedding model"
                )
            if not _plain_url(self.memory_provider_url):
                raise ValueError(
                    "Memory endpoints require HTTPS, except on localhost or a private network"
                )
        for value in (self.mcp_resource_url, self.web_url, self.memory_embedding_url):
            if value and not _plain_url(value):
                raise ValueError(
                    "Service URLs require HTTPS, except on localhost or a private network"
                )
        if not self.mcp_resource_url.endswith("/mcp/"):
            raise ValueError("MCP resource URL must end with /mcp/")
        if self.environment == "production" and not self.database_url:
            raise ValueError("Production requires SPACE_DATABASE_URL")
        return self


def database_connection_options(settings) -> dict:
    """libpq TLS options for every runtime and worker connection."""
    options = {"sslmode": settings.database_sslmode}
    if settings.database_sslmode in {"verify-ca", "verify-full"}:
        options["sslrootcert"] = str(settings.database_ca_file or "system")
    elif settings.database_ca_file:
        options["sslrootcert"] = str(settings.database_ca_file)
    return options
