"""OAuth for MCP clients that connect through a browser sign-in.

Hosted assistants such as claude.ai and ChatGPT, and editors without a header setting,
add Space by its MCP URL alone. In key mode the MCP endpoint answers them 401 with the
address of this server's metadata (RFC 9728). They register themselves (RFC 7591) and
send the owner's browser to /oauth/authorize; the owner unlocks the website and chooses
the agent identity and access on /connect/authorize. The client then exchanges the
one-time code, with its PKCE verifier, for an access token and a rotating refresh token.

Tokens stand for an ordinary connection grant, so the Agents page lists, attributes and
revokes them like every other connection. Client secrets, codes and tokens are stored
as SHA-256 digests. Private mode issues no tokens, so it mounts none of this.
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import replace
from datetime import timedelta
from typing import Literal
from urllib.parse import unquote, urlencode, urlsplit
from uuid import UUID

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler, ProtectedResourceMetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.server.streamable_http import MCP_PROTOCOL_VERSION_HEADER
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb
from pydantic import AnyHttpUrl, AnyUrl, BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.routing import Route, request_response

from space.security.auth import OAUTH_TOKEN_PREFIX, Identity
from space.settings import Settings
from space.workspace import CommandError

REFRESH_TOKEN_PREFIX = "spr_"  # noqa: S105 - a public prefix, not a credential
# How long the owner has to answer on the website, then how long the code lives.
REQUEST_LIFETIME = timedelta(minutes=10)
CODE_LIFETIME = timedelta(minutes=10)
ACCESS_LIFETIME = timedelta(hours=1)
# Each refresh issues a new pair, so a client in use never reaches this.
REFRESH_LIFETIME = timedelta(days=180)
MAX_REDIRECT_URIS = 10
MAX_CLIENT_METADATA = 16384
MAX_SCOPES = 1024
MAX_STATE = 2048
CLIENT_NAME_LIMIT = 80
# PKCE S256: the unpadded base64url SHA-256 of the client's verifier.
S256_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Script-bearing and browser-internal schemes never receive a code. Any other scheme
# names an app on the owner's computer (RFC 8252, section 7.1), such as cursor://.
UNSAFE_SCHEMES = frozenset(
    {
        "about",
        "blob",
        "data",
        "file",
        "filesystem",
        "ftp",
        "intent",
        "javascript",
        "mailto",
        "sms",
        "tel",
        "vbscript",
        "view-source",
        "ws",
        "wss",
    }
)
SCHEME = re.compile(r"[a-z][a-z0-9+.-]{0,62}")
GONE = (
    "This connection request expired or was already answered. Start the connection again "
    "from your app."
)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def resource_url(settings: Settings) -> str:
    """The MCP endpoint as OAuth names it, without the trailing slash, so clients
    configured with either form accept it (RFC 9728, section 3.3)."""
    return settings.mcp_resource_url.rstrip("/")


def issuer_url(settings: Settings) -> str:
    """The authorization server: the public origin that serves /mcp/ and the endpoints
    below. A Compose installation serves the website there too; in development the API
    has an origin of its own."""
    url = urlsplit(settings.mcp_resource_url)
    return f"{url.scheme}://{url.netloc}"


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    if url.scheme not in ("http", "https") or not url.hostname:
        return None
    return url.scheme, url.hostname.lower(), port or (443 if url.scheme == "https" else 80)


def redirect_allowed(value: str) -> bool:
    """Parameters: a redirect URI a client registers. Output: whether codes may go there:
    HTTPS anywhere, plain HTTP only on the loopback interface (RFC 8252, section 7.3) or
    an app's own scheme; never a fragment, credentials or a script-bearing scheme."""
    if not value or len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return False
    try:
        url = urlsplit(value)
        host = url.hostname
    except ValueError:
        return False
    if url.fragment or url.username is not None or url.password is not None:
        return False
    scheme = url.scheme.lower()
    if scheme == "https":
        return bool(host)
    if scheme == "http":
        return host in LOOPBACK_HOSTS
    return bool(SCHEME.fullmatch(scheme)) and scheme not in UNSAFE_SCHEMES


def _loopback(value: str) -> bool:
    url = urlsplit(value)
    return url.scheme == "http" and url.hostname in LOOPBACK_HOSTS


def _same_except_port(presented: str, registered: str) -> bool:
    first, second = urlsplit(presented), urlsplit(registered)
    return (
        _loopback(registered)
        and first.hostname == second.hostname
        and first.path == second.path
        and first.query == second.query
    )


def client_name(value: str | None) -> str | None:
    """The name a client registered, as the consent page may show it: visible
    characters and single spaces, at most 80 characters."""
    if not value:
        return None
    visible = (
        c
        for c in unicodedata.normalize("NFKC", value)
        if not unicodedata.category(c).startswith("C")
    )
    text = "".join(visible)
    return " ".join(text.split())[:CLIENT_NAME_LIMIT].strip() or None


class RegisteredClient(OAuthClientInformationFull):
    """A registered client as the SDK handlers see it. Its secret is known by digest only."""

    secret_hash: bytes | None = Field(default=None, exclude=True)

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        # Space has no OAuth scopes: the owner chooses read or write access during
        # consent. Requested scopes are recorded and echoed back, never refused.
        return requested_scope.split() if requested_scope else None

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        # Native clients listen on whichever loopback port is free when they connect,
        # so a loopback redirect may change its port (RFC 8252, section 7.3).
        if redirect_uri is not None and _loopback(str(redirect_uri)):
            for registered in self.redirect_uris or []:
                if _same_except_port(str(redirect_uri), str(registered)):
                    return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


class DigestClientAuthenticator(ClientAuthenticator):
    """The SDK's client authentication for the token and revocation endpoints, with the
    presented secret compared by digest, since Space keeps no client secret."""

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form = await request.form()
        client_id = form.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise AuthenticationError("Missing client_id")
        client = await self.provider.get_client(client_id)
        if client is None:
            raise AuthenticationError("Invalid client_id")
        presented = None
        method = client.token_endpoint_auth_method
        if method == "client_secret_basic":
            header = request.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                raise AuthenticationError("Missing or invalid Basic authentication")
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode()
                basic_id, presented = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                raise AuthenticationError("Invalid Basic authentication header") from None
            if unquote(basic_id) != client_id:
                raise AuthenticationError("Client ID mismatch in Basic authentication")
            presented = unquote(presented)
        elif method == "client_secret_post":
            value = form.get("client_secret")
            presented = value if isinstance(value, str) else None
        elif method != "none":
            raise AuthenticationError("Unsupported client authentication method")
        if client.secret_hash is not None and (
            not presented or not hmac.compare_digest(client.secret_hash, _digest(presented))
        ):
            raise AuthenticationError("Invalid client_secret")
        return client


def _new_pair() -> tuple[str, str]:
    return (
        OAUTH_TOKEN_PREFIX + secrets.token_urlsafe(32),
        REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32),
    )


def _token_response(access: str, refresh: str, scopes: str) -> OAuthToken:
    return OAuthToken(
        access_token=access,
        expires_in=int(ACCESS_LIFETIME.total_seconds()),
        refresh_token=refresh,
        scope=scopes or None,
    )


class AuthorizationServer:
    """The SDK's authorization server provider, backed by the oauth_* definer functions.

    Every step that needs no person runs in a system transaction; consent happens on the
    website through details() and decide() below.
    """

    def __init__(self, workspace, settings: Settings):
        self.db = workspace.db
        self.consent_url = settings.web_url.rstrip("/") + "/connect/authorize"
        self.origin = _origin(settings.mcp_resource_url)

    def _row(self, sql: str, params: tuple):
        with self.db.system_transaction() as connection:
            return connection.execute(sql, params).fetchone()

    async def _call(self, sql: str, params: tuple):
        return await asyncio.to_thread(self._row, sql, params)

    async def get_client(self, client_id: str) -> RegisteredClient | None:
        try:
            key = UUID(client_id)
        except (TypeError, ValueError):
            return None
        row = await self._call("SELECT * FROM space.oauth_client(%s)", (key,))
        if row is None:
            return None
        return RegisteredClient.model_validate(
            {**row["details"], "client_id": str(key), "secret_hash": row["secret_hash"]}
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(uri) for uri in client_info.redirect_uris or []]
        if not uris or len(uris) > MAX_REDIRECT_URIS or not all(map(redirect_allowed, uris)):
            raise RegistrationError(
                "invalid_redirect_uri",
                "Register up to ten redirect URIs that use HTTPS, HTTP on a loopback "
                "address, or the app's own scheme.",
            )
        # The registration response carries the cleaned name, as the owner will see it.
        client_info.client_name = client_name(client_info.client_name)
        details = client_info.model_dump(
            mode="json", exclude={"client_id", "client_secret"}, exclude_none=True
        )
        if len(json.dumps(details).encode()) > MAX_CLIENT_METADATA:
            raise RegistrationError("invalid_client_metadata", "Client metadata is too large.")
        secret = client_info.client_secret
        await self._call(
            "SELECT space.oauth_register(%s,%s,%s)",
            (UUID(client_info.client_id), _digest(secret) if secret else None, Jsonb(details)),
        )

    async def authorize(self, client: RegisteredClient, params: AuthorizationParams) -> str:
        if params.resource is not None and _origin(params.resource) != self.origin:
            raise AuthorizeError(
                "invalid_request", "This server issues tokens for its own MCP endpoint only."
            )
        if not S256_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "Send a PKCE S256 code challenge.")
        if params.state is not None and len(params.state) > MAX_STATE:
            raise AuthorizeError("invalid_request", "The state parameter is too long.")
        scopes = " ".join(params.scopes or [])
        if len(scopes) > MAX_SCOPES:
            raise AuthorizeError("invalid_scope", "Too many scopes requested.")
        try:
            row = await self._call(
                "SELECT space.oauth_begin(%s,%s,%s,%s,%s,%s,%s,%s) AS id",
                (
                    UUID(client.client_id),
                    str(params.redirect_uri),
                    params.redirect_uri_provided_explicitly,
                    params.code_challenge,
                    params.state,
                    scopes,
                    params.resource,
                    REQUEST_LIFETIME,
                ),
            )
        except RaiseException as error:
            if error.diag.message_primary == "space.oauth_busy":
                raise AuthorizeError(
                    "temporarily_unavailable",
                    "Too many pending connection requests. Try again in a few minutes.",
                ) from None
            raise
        return f"{self.consent_url}?{urlencode({'authorization_id': str(row['id'])})}"

    async def load_authorization_code(
        self, client: RegisteredClient, authorization_code: str
    ) -> AuthorizationCode | None:
        if len(authorization_code) > 256:
            return None
        row = await self._call(
            "SELECT * FROM space.oauth_code(%s,%s)",
            (_digest(authorization_code), UUID(client.client_id)),
        )
        if row is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["requested_scopes"].split(),
            expires_at=row["valid_until"].timestamp(),
            client_id=client.client_id,
            code_challenge=row["challenge"],
            redirect_uri=AnyUrl(row["redirect"]),
            redirect_uri_provided_explicitly=row["explicit"],
            resource=row["target"],
        )

    async def exchange_authorization_code(
        self, client: RegisteredClient, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        access, refresh = _new_pair()
        row = await self._call(
            "SELECT * FROM space.oauth_redeem(%s,%s,%s,%s,%s,%s)",
            (
                _digest(authorization_code.code),
                UUID(client.client_id),
                _digest(access),
                ACCESS_LIFETIME,
                _digest(refresh),
                REFRESH_LIFETIME,
            ),
        )
        if row is None:
            raise TokenError(
                "invalid_grant",
                "The authorization code was already used, has expired, or its connection "
                "was revoked.",
            )
        return _token_response(access, refresh, row["requested_scopes"])

    async def load_refresh_token(
        self, client: RegisteredClient, refresh_token: str
    ) -> RefreshToken | None:
        if not refresh_token.startswith(REFRESH_TOKEN_PREFIX) or len(refresh_token) > 256:
            return None
        row = await self._call(
            "SELECT * FROM space.oauth_refresh_token(%s,%s)",
            (_digest(refresh_token), UUID(client.client_id)),
        )
        if row is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=row["requested_scopes"].split(),
            expires_at=int(row["valid_until"].timestamp()),
            resource=row["target"],
        )

    async def exchange_refresh_token(
        self, client: RegisteredClient, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        access, refresh = _new_pair()
        row = await self._call(
            "SELECT space.oauth_rotate(%s,%s,%s,%s,%s,%s) AS rotated",
            (
                _digest(refresh_token.token),
                UUID(client.client_id),
                _digest(access),
                ACCESS_LIFETIME,
                _digest(refresh),
                REFRESH_LIFETIME,
            ),
        )
        if not row["rotated"]:
            raise TokenError(
                "invalid_grant", "The refresh token is no longer valid. Connect again."
            )
        # Scopes carry no access in Space; the pair keeps the ones first granted.
        return _token_response(access, refresh, " ".join(refresh_token.scopes))

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not token.startswith(OAUTH_TOKEN_PREFIX) or len(token) > 256:
            return None
        row = await self._call("SELECT * FROM space.oauth_token_identity(%s)", (_digest(token),))
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(row["client"]),
            scopes=row["requested_scopes"].split(),
            expires_at=int(row["valid_until"].timestamp()),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._call(
            "SELECT space.oauth_revoke(%s,%s)", (_digest(token.token), UUID(token.client_id))
        )


class OAuthTokens:
    """Resolves OAuth access tokens for the MCP token verifier."""

    def __init__(self, database, issuer: str):
        self.database, self.issuer = database, issuer

    def identity(self, token: str) -> Identity | None:
        with self.database.system_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM space.oauth_token_identity(%s)", (_digest(token),)
            ).fetchone()
        if row is None or row["issuer"] != self.issuer:
            return None
        return Identity(
            row["issuer"],
            row["subject"],
            int(row["valid_until"].timestamp()),
            str(row["client"]),
            str(row["grant_row"]),
        )


class Consent(BaseModel):
    """The owner's answer on /connect/authorize."""

    model_config = ConfigDict(extra="forbid")
    authorization_id: UUID
    action: Literal["approve", "deny"]
    can_write: bool = False
    agent_id: UUID | None = None
    space_id: UUID | None = None


def _details(authorization_id: UUID, row) -> dict:
    metadata = row["details"]
    return {
        "authorization_id": str(authorization_id),
        "client": {
            "id": str(row["client"]),
            "name": metadata.get("client_name") or "An MCP client",
            "uri": metadata.get("client_uri"),
        },
        "redirect_uri": row["redirect"],
        "scope": row["requested_scopes"],
    }


def details(workspace, identity: Identity, authorization_id: UUID) -> dict:
    """Parameters: the owner and a pending request. Output: what the consent page shows:
    the client's registered name and where the browser returns afterwards."""
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        row = connection.execute(
            "SELECT * FROM space.oauth_request(%s)", (authorization_id,)
        ).fetchone()
    if row is None:
        raise CommandError(GONE, 404)
    return _details(authorization_id, row)


def decide(workspace, identity: Identity, body: Consent) -> dict:
    """Parameters: the owner's decision. What it does: on approval creates the client's
    connection grant and a one-time code in one transaction; a denial drops the request.
    Output: the client's redirect URL carrying the code, or the refusal, and its state."""
    identity.check(human=True)
    approve = body.action == "approve"
    actor = replace(identity, selected_space_id=body.space_id)
    code = secrets.token_urlsafe(32)
    with workspace.db.transaction(actor) as connection:
        request = connection.execute(
            "SELECT * FROM space.oauth_request(%s)", (body.authorization_id,)
        ).fetchone()
        if request is None:
            raise CommandError(GONE, 409)
        grant_id = (
            workspace._grant(connection, actor, request["client"], body.can_write, body.agent_id)
            if approve
            else None
        )
        row = connection.execute(
            "SELECT * FROM space.oauth_decide(%s,%s,%s,%s,%s)",
            (
                body.authorization_id,
                approve,
                _digest(code) if approve else None,
                UUID(grant_id) if grant_id else None,
                CODE_LIFETIME,
            ),
        ).fetchone()
        if row is None:
            # Rolls back the grant with it.
            raise CommandError(GONE, 409)
    answer = (
        {"code": code}
        if approve
        else {"error": "access_denied", "error_description": "The owner declined the connection."}
    )
    return {
        "redirect_url": construct_redirect_uri(
            row["redirect"], state=row["request_state"], **answer
        )
    }


def _cors(handler, methods: list[str]) -> CORSMiddleware:
    # Browser-based MCP clients call these endpoints from their own origin; the
    # authorization endpoint itself is a top-level navigation and takes no CORS.
    return CORSMiddleware(
        request_response(handler),
        allow_origins=["*"],
        allow_methods=methods,
        allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
    )


def routes(workspace, settings: Settings) -> list[Route]:
    """Parameters: the workspace and settings. Output: the discovery documents and the
    OAuth endpoints, served at the root of the public address next to /mcp/."""
    provider = AuthorizationServer(workspace, settings)
    clients = DigestClientAuthenticator(provider)
    base = issuer_url(settings)
    issuer = AnyHttpUrl(base)
    server = OAuthMetadata(
        issuer=issuer,
        authorization_endpoint=AnyHttpUrl(f"{base}/oauth/authorize"),
        token_endpoint=AnyHttpUrl(f"{base}/oauth/token"),
        registration_endpoint=AnyHttpUrl(f"{base}/oauth/register"),
        revocation_endpoint=AnyHttpUrl(f"{base}/oauth/revoke"),
        revocation_endpoint_auth_methods_supported=["client_secret_post", "client_secret_basic"],
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods_supported=["client_secret_post", "client_secret_basic", "none"],
        code_challenge_methods_supported=["S256"],
    )
    resource = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource_url(settings)),
        authorization_servers=[issuer],
        resource_name="Space",
    )
    discovery = ["GET", "OPTIONS"]
    endpoint = ["POST", "OPTIONS"]
    described = _cors(ProtectedResourceMetadataHandler(resource).handle, discovery)
    return [
        Route(
            "/.well-known/oauth-authorization-server",
            _cors(MetadataHandler(server).handle, discovery),
            methods=discovery,
        ),
        # The resource path is inserted after the well-known prefix (RFC 9728, section 3.1);
        # clients that drop the trailing slash, or the path, look in the other two places.
        *(
            Route(path, described, methods=discovery)
            for path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource/mcp/",
            )
        ),
        Route("/oauth/authorize", AuthorizationHandler(provider).handle, methods=["GET", "POST"]),
        Route(
            "/oauth/token",
            _cors(TokenHandler(provider, clients).handle, endpoint),
            methods=endpoint,
        ),
        Route(
            "/oauth/register",
            _cors(
                RegistrationHandler(provider, ClientRegistrationOptions(enabled=True)).handle,
                endpoint,
            ),
            methods=endpoint,
        ),
        Route(
            "/oauth/revoke",
            _cors(RevocationHandler(provider, clients).handle, endpoint),
            methods=endpoint,
        ),
    ]
