"""The production verifier: access key, private mode and Space-issued tokens.

The `signing` fixture below is a test-only stand-in for an external identity provider.
REST and MCP transport tests use it to act as different people and as delegated agents
with chosen grants, which the single-owner production verifier cannot express. The
transports only consume the Identity a verifier returns, so they behave the same.
"""

import json
import logging
import time
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from space.security.auth import (
    API_KEY_PREFIX,
    ISSUER,
    OWNER_SUBJECT,
    SPACE_TOKEN_PREFIX,
    Identity,
    TokenVerifier,
    Unauthenticated,
)
from space.settings import Settings, private_host

ACCESS_KEY = "synthetic-access-key-" + "k" * 32


class JwtVerifier(TokenVerifier):
    """Test-only verifier for ES256 tokens signed by the fixture key."""

    def __init__(self, jwk):
        super().__init__(Settings(environment="test"))
        self.issuer = "https://identity.invalid/auth/v1"
        self.jwk = jwk

    def verify(self, token, *, mcp=False):
        if token and token.startswith((SPACE_TOKEN_PREFIX, API_KEY_PREFIX)):
            return super().verify(token, mcp=mcp)
        stage = "token_shape"
        try:
            if not token or len(token) > 16384:
                raise ValueError("Invalid token size")
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "ES256" or header.get("kid") != self.jwk["kid"]:
                raise ValueError("Unsupported signing key")
            stage = "claims_verification"
            audience = self.resource if mcp else "authenticated"
            claims = jwt.decode(
                token,
                jwt.PyJWK(self.jwk).key,
                algorithms=["ES256"],
                issuer=self.issuer,
                audience=audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if claims["aud"] != audience and claims["aud"] != [audience]:
                raise ValueError("Exactly one intended audience is required")
            if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
                raise ValueError("An authenticated account is required")
            subject = str(UUID(claims["sub"]))
            if mcp:
                client = str(UUID(claims["client_id"]))
                grant = str(UUID(claims["space_grant_id"]))
            else:
                if claims.get("client_id") is not None:
                    raise ValueError("Delegated tokens cannot administer access")
                client, grant = None, None
            identity = Identity(self.issuer, subject, int(claims["exp"]), client, grant)
            identity.check()
            return identity
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            logging.getLogger("space.auth").warning(
                "Access token rejected: stage=%s reason=%s", stage, type(exc).__name__
            )
            raise Unauthenticated("Invalid or expired access token") from exc


@pytest.fixture
def signing():
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", alg="ES256")
    verifier = JwtVerifier(jwk)
    claims = {
        "iss": verifier.issuer,
        "aud": "authenticated",
        "sub": str(uuid4()),
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "role": "authenticated",
    }

    def token(**overrides):
        return jwt.encode(
            {**claims, **overrides}, key, algorithm="ES256", headers={"kid": "test-key"}
        )

    return verifier, token


def key_verifier(**values):
    return TokenVerifier(Settings(environment="test", access_key=ACCESS_KEY, **values))


def test_the_access_key_acts_as_the_single_owner_on_rest_only():
    verifier = key_verifier()
    owner = verifier.verify(ACCESS_KEY)
    assert (owner.issuer, owner.subject, owner.client_id, owner.grant_id) == (
        ISSUER,
        OWNER_SUBJECT,
        None,
        None,
    )
    owner.check(human=True)
    for token in (None, "", ACCESS_KEY + "x", ACCESS_KEY[:-1], "x" * 600):
        with pytest.raises(Unauthenticated):
            verifier.verify(token)
    with pytest.raises(Unauthenticated):
        verifier.verify(ACCESS_KEY, mcp=True)


def test_private_mode_admits_the_owner_without_credentials_but_not_agents():
    verifier = TokenVerifier(Settings(environment="test", access_mode="private"))
    assert verifier.private
    for token in (None, "anything"):
        assert verifier.verify(token).subject == OWNER_SUBJECT
    with pytest.raises(Unauthenticated):
        verifier.verify(None, mcp=True)


def test_space_tokens_and_api_keys_keep_to_their_transport_and_need_a_store():
    verifier = key_verifier()
    for token, mcp in (
        (SPACE_TOKEN_PREFIX + "x" * 40, True),
        (API_KEY_PREFIX + "x" * 40, False),
        (SPACE_TOKEN_PREFIX + "x" * 40, False),
        (API_KEY_PREFIX + "x" * 40, True),
    ):
        with pytest.raises(Unauthenticated):
            verifier.verify(token, mcp=mcp)

    class Store:
        def __init__(self, issuer):
            self.issuer = issuer

        def identity(self, token):
            return Identity(self.issuer, OWNER_SUBJECT, int(time.time()) + 60, "c", "g")

    verifier.space_tokens = Store(ISSUER)
    assert verifier.verify(SPACE_TOKEN_PREFIX + "x" * 40, mcp=True).client_id == "c"
    verifier.space_tokens = Store("https://another.invalid")
    with pytest.raises(Unauthenticated):
        verifier.verify(SPACE_TOKEN_PREFIX + "x" * 40, mcp=True)


def test_rejections_log_a_category_without_the_credential(caplog):
    verifier = key_verifier()
    with pytest.raises(Unauthenticated):
        verifier.verify("not-the-key-" + "s" * 30)
    assert "stage=access_key" in caplog.text
    assert "not-the-key" not in caplog.text


def test_key_mode_requires_a_long_access_key_once_a_database_is_configured():
    database = "postgresql://space_app:password@db:5432/space"
    with pytest.raises(ValidationError, match="SPACE_ACCESS_KEY"):
        Settings(environment="test", database_url=database)
    with pytest.raises(ValidationError):
        Settings(environment="test", database_url=database, access_key="short")
    Settings(environment="test", database_url=database, access_key=ACCESS_KEY)
    Settings(environment="test", database_url=database, access_mode="private")


@pytest.mark.parametrize(
    ("host", "private"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("10.0.0.5", True),
        ("192.168.1.20", True),
        ("100.101.102.103", True),
        ("ollama", True),
        ("space.tailnet.ts.net", True),
        ("box.local", True),
        ("example.com", False),
        ("8.8.8.8", False),
        ("api.openai.com", False),
        ("", False),
    ],
)
def test_plain_http_is_limited_to_private_hosts(host, private):
    assert private_host(host) is private


def test_service_urls_refuse_plain_http_on_public_hosts():
    Settings(environment="test", web_url="http://localhost:8080")
    Settings(environment="test", web_url="http://100.64.0.7:8080")
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(environment="test", web_url="http://space.example.com")
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            environment="test",
            memory_provider="openai_compatible",
            memory_provider_url="http://api.example.com/v1",
            memory_text_model="model",
            memory_embedding_model="embedding",
        )
    Settings(
        environment="test",
        memory_provider="ollama",
        memory_provider_url="http://ollama:11434",
        memory_text_model="model",
        memory_embedding_model="embedding",
    )


def test_blank_settings_fall_back_to_their_defaults(monkeypatch):
    for name in (
        "SPACE_MEMORY_REASONING_EFFORT",
        "SPACE_MEMORY_RELEVANCE_REASONING_EFFORT",
        "SPACE_MEMORY_PROVIDER_URL",
        "SPACE_ACCESS_KEY",
        "SPACE_OPS_TOKEN",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("SPACE_WEB_URL", " ")
    settings = Settings(_env_file=None, environment="test")
    assert settings.memory_reasoning_effort is None
    assert settings.memory_provider_url is None
    assert settings.access_key is None and settings.ops_token is None
    assert settings.web_url == "http://127.0.0.1:3000"
