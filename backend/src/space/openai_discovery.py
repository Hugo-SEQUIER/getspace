"""Let OpenAI's clients see Space's tools before signing in.

OpenAI's "OAuth or no authentication" mode (mixed authentication) expects initialize and
tools/list to answer without a token: its plugin portal scans a server's tools that way,
and its scanner sends no token even when the plugin is set to OAuth alone. In that mode
ChatGPT asks the person to connect only when a tool call answers with a sign-in challenge
in `_meta["mcp/www_authenticate"]`, not with an HTTP 401.

Every other MCP client learns that Space needs sign-in from the 401 on its first request,
so only requests from OpenAI's clients that carry no token Space accepts are treated this
way. Their tool calls, reads and prompts still need a token, and nothing runs without one.
"""

import json
import re

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, RequireAuthMiddleware
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

# ChatGPT's connector, and the plugin portal's scanner, which sends aiohttp's default.
OPENAI_CLIENTS = re.compile(r"(openai-mcp/|Python/\d+\.\d+ aiohttp/)")
# What the server is and which tools, prompts and resources it offers, never their content.
DISCOVERY = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
    }
)
# Discovery and a first tool call are small; a larger request keeps the 401.
MAX_INSPECTED_BODY = 64 * 1024
SIGN_IN = "Connect your Space account to use this tool."
# Declared on every tool, at the top level and in `_meta`: each one needs a signed-in account.
SECURITY_SCHEMES = [{"type": "oauth2", "scopes": []}]


class OpenAIDiscovery:
    """Wraps the SDK's RequireAuthMiddleware on the MCP endpoint."""

    def __init__(self, protected: RequireAuthMiddleware):
        self.protected = protected
        self.challenge = f'Bearer error="invalid_token", error_description="{SIGN_IN}"'
        if protected.resource_metadata_url:
            self.challenge += f', resource_metadata="{protected.resource_metadata_url}"'

    async def __call__(self, scope, receive, send):
        headers = Headers(scope=scope)
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or isinstance(scope.get("user"), AuthenticatedUser)
            or not OPENAI_CLIENTS.match(headers.get("user-agent", ""))
            or not headers.get("content-type", "").startswith("application/json")
        ):
            await self.protected(scope, receive, send)
            return
        body, complete = b"", False
        while not complete and len(body) <= MAX_INSPECTED_BODY:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            complete = not message.get("more_body", False)
        replay = _replay(body, complete, receive)
        messages = _messages(body) if complete and len(body) <= MAX_INSPECTED_BODY else []
        call = messages[0] if len(messages) == 1 else {}
        anonymous = "authorization" not in headers
        if anonymous and messages and all(item.get("method") in DISCOVERY for item in messages):
            await self.protected.app(scope, replay, send)
        elif call.get("method") == "tools/call" and "id" in call:
            await self._sign_in(call["id"])(scope, replay, send)
        else:
            await self.protected(scope, replay, send)

    def _sign_in(self, request_id):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": SIGN_IN}],
                    "isError": True,
                    "_meta": {"mcp/www_authenticate": [self.challenge]},
                },
            }
        )


def _messages(body):
    """The JSON-RPC messages in a request body, or [] when it is not one."""
    try:
        value = json.loads(body)
    except ValueError:
        return []
    messages = value if isinstance(value, list) else [value]
    return messages if messages and all(isinstance(item, dict) for item in messages) else []


def _replay(body, complete, receive):
    """Hand the bytes already read to the next application, then the rest of the stream."""
    sent = False

    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": not complete}

    return replay
