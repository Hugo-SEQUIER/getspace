"""Resolve request credentials to the installation owner, an agent connection or an API key.

The self-hosted edition has no user accounts. One implicit owner holds the Private Space.

- Key mode: the website and the owner's own scripts present the instance access key.
  Agents present Space-issued connection tokens (`spc_…`) or OAuth access tokens
  (`spo_…`, see space.oauth_server) on MCP, and personal API keys (`spk_…`) reach
  REST only.
- Private mode: nothing is checked. The operator keeps the instance on a private network,
  and every REST request acts as the owner.
"""

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from mcp.server.auth.provider import AccessToken

from space.settings import Settings


class Unauthenticated(Exception):
    pass


# The identity issuer stored with every principal of this installation. It never
# depends on the public URL, so moving the instance to a new domain keeps its data.
ISSUER = "space"
# The single implicit owner. A constant, so the owner's Space survives restarts and
# access-key rotation without any account record.
OWNER_SUBJECT = str(UUID("5ba5e000-0000-4000-8000-000000000001"))
# Owner identities are re-derived on every request; this bounds one request's lifetime.
OWNER_SESSION_SECONDS = 3600

# Bearer tokens Space issues itself for agent connections, from the website or the
# device-code flow. The prefix routes them to the database lookup.
SPACE_TOKEN_PREFIX = "spc_"  # noqa: S105 - a public prefix, not a credential
# Personal API keys for the owner's own clients. REST only, never MCP.
API_KEY_PREFIX = "spk_"  # noqa: S105 - a public prefix, not a credential
# OAuth access tokens for clients that connect through a browser sign-in. MCP only.
OAUTH_TOKEN_PREFIX = "spo_"  # noqa: S105 - a public prefix, not a credential


@dataclass(frozen=True)
class ApiKeyScope:
    """What one personal API key may reach: a single Space, read-only or read-write."""

    id: str
    space_id: UUID
    can_write: bool


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    expires_at: int
    client_id: str | None = None
    grant_id: str | None = None
    agent_id: str | None = None
    # Browser-selected default; this is a selector, never an authorization claim.
    selected_space_id: UUID | None = None
    api_key: ApiKeyScope | None = None

    def check(self, *, human: bool = False, api_key: bool = False) -> None:
        """human refuses agents and, unless api_key is set, the owner's API keys too."""
        if self.expires_at <= time.time():
            raise Unauthenticated("Unlock again")
        if human and self.client_id is not None:
            raise Unauthenticated("Agents cannot perform this operation")
        if human and self.api_key is not None and not api_key:
            raise Unauthenticated("API keys cannot perform this operation. Use the website.")


def owner_identity() -> Identity:
    return Identity(ISSUER, OWNER_SUBJECT, int(time.time()) + OWNER_SESSION_SECONDS)


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.issuer = ISSUER
        self.resource = settings.mcp_resource_url
        self.private = settings.access_mode == "private"
        self._access_key = (
            settings.access_key.get_secret_value().encode() if settings.access_key else None
        )
        # Set by the application once a database exists; None rejects Space tokens,
        # OAuth tokens and API keys.
        self.space_tokens = None
        self.oauth_tokens = None
        self.api_keys = None

    def verify(self, token: str | None, *, mcp: bool = False) -> Identity:
        if token and token.startswith(SPACE_TOKEN_PREFIX):
            return self._verify_issued(token, self.space_tokens, mcp, "space_token")
        if token and token.startswith(OAUTH_TOKEN_PREFIX):
            return self._verify_issued(token, self.oauth_tokens, mcp, "oauth_token")
        if token and token.startswith(API_KEY_PREFIX):
            return self._verify_issued(token, self.api_keys, not mcp, "api_key")
        if not mcp:
            if self.private:
                return owner_identity()
            if (
                token
                and self._access_key
                and len(token) <= 512
                and hmac.compare_digest(token.encode(), self._access_key)
            ):
                return owner_identity()
        # Diagnostic categories only: never record credentials.
        logging.getLogger("space.auth").warning(
            "Access token rejected: stage=%s", "agent_token" if mcp else "access_key"
        )
        raise Unauthenticated(
            "Agent token required" if mcp else "Unlock this Space with its access key"
        )

    def _verify_issued(self, token: str, store, accepted: bool, stage: str) -> Identity:
        """Resolve a token Space issued itself; accepted says whether this transport
        takes that kind (agent tokens on MCP, API keys on REST)."""
        try:
            if not accepted or store is None or len(token) > 256:
                raise ValueError("Token kind not accepted on this transport")
            stage += "_lookup"
            identity = store.identity(token)
            if identity is None or identity.issuer != self.issuer:
                raise ValueError("Unknown, expired or revoked token")
            stage = "expiry"
            identity.check()
            return identity
        except (ValueError, Unauthenticated) as exc:
            logging.getLogger("space.auth").warning(
                "Access token rejected: stage=%s reason=%s", stage, type(exc).__name__
            )
            raise Unauthenticated("Invalid or expired access token") from exc

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            identity = await asyncio.to_thread(self.verify, token, mcp=True)
        except Unauthenticated:
            return None
        return AccessToken(
            token=token,
            client_id=identity.client_id,
            scopes=[],
            expires_at=identity.expires_at,
            resource=self.resource,
            subject=identity.subject,
            claims={"iss": identity.issuer, "space_grant_id": identity.grant_id},
        )
