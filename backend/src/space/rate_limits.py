"""Per-client limits on what anyone can ask without credentials.

Registering an OAuth client, starting an authorization or a device code, and guessing the
access key need no account. Each is counted per client address, in memory, so that one client
cannot fill the pending queues or try keys without end. The website limits its own unlock form
the same way (apps/web/lib/access.ts); this covers the API, which Caddy serves directly.
"""

import ipaddress
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

# Parameters: none. The address a request is counted against when none can be read.
UNKNOWN = "unknown"


def _address(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


# Where a reverse proxy on the same host or network forwards from, as the website's `nearbyHop`:
# loopback, RFC 1918, link-local and unique local addresses. `is_private` would also trust
# documentation and other reserved ranges.
NEARBY = [
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
]


def _nearby(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip in network for network in NEARBY)


def client_address(scope: dict) -> str:
    """Parameters: an ASGI scope. What it does: trusts the last X-Forwarded-For hop only when
    the peer is a nearby proxy (loopback, private or link-local), as Caddy in front of the API
    is; a direct client cannot choose its address that way. Output: the address to count."""
    client = scope.get("client")
    peer = _address(client[0] if client else None)
    if peer and _nearby(peer):
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                hops = [hop for hop in value.decode("latin-1").split(",") if hop.strip()]
                hop = _address(hops[-1]) if hops else None
                if hop:
                    return hop
    return peer or UNKNOWN


class Window:
    """Counts events per key over a sliding window, in memory; a restart forgets them. The least
    recently counted keys are forgotten first beyond `capacity`."""

    def __init__(self, limit: int, seconds: float, capacity: int = 10_000):
        self.limit, self.seconds, self.capacity = limit, seconds, capacity
        self._events: OrderedDict[str, list[float]] = OrderedDict()

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._events.get(key, []) if now - t < self.seconds]
        if times:
            self._events[key] = times
        else:
            self._events.pop(key, None)
        return times

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Parameters: a key. Output: seconds until it may be counted again, 0 when it may."""
        now = time.monotonic() if now is None else now
        times = self._recent(key, now)
        if len(times) < self.limit:
            return 0
        return max(1, int(times[0] + self.seconds - now) + 1)

    def record(self, key: str, now: float | None = None) -> None:
        """Parameters: a key. What it does: counts one event for it. Output: none."""
        now = time.monotonic() if now is None else now
        times = [*self._recent(key, now), now][-self.limit :]
        self._events.pop(key, None)
        self._events[key] = times
        while len(self._events) > self.capacity:
            self._events.popitem(last=False)


@dataclass(frozen=True)
class Rule:
    """A request the API limits: its methods, a test on its path, and its window."""

    methods: frozenset[str]
    matches: Callable[[str], bool]
    window: Window


def default_rules() -> list[Rule]:
    """Output: the limits on what needs no credentials. A person connecting an app or an agent
    makes a few of these requests; a client that makes hundreds is filling a queue."""
    return [
        Rule(frozenset({"POST"}), lambda p: p == "/oauth/register", Window(20, 600)),
        Rule(frozenset({"GET", "POST"}), lambda p: p == "/oauth/authorize", Window(60, 600)),
        Rule(frozenset({"POST"}), lambda p: p == "/v1/device/code", Window(30, 600)),
    ]


# Wrong access keys or API keys allowed per client address, as on the website's unlock form.
KEY_FAILURES = 10
KEY_WINDOW = 15 * 60


class PreAuthLimits:
    """ASGI middleware. Answers 429 with Retry-After when a client exceeds a rule, and when it
    has sent too many wrong credentials to /v1, where each 401 counts as one failure."""

    def __init__(self, app, rules: list[Rule] | None = None, failures: Window | None = None):
        self.app = app
        self.rules = default_rules() if rules is None else rules
        self.failures = failures or Window(KEY_FAILURES, KEY_WINDOW)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        client = client_address(scope)
        for rule in self.rules:
            if method in rule.methods and rule.matches(path):
                wait = rule.window.retry_after(client)
                if wait:
                    await _too_many(send, wait)
                    return
                rule.window.record(client)
        if not path.startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        credentials = any(name == b"authorization" for name, _ in scope.get("headers", []))
        wait = self.failures.retry_after(client) if credentials else 0
        if wait:
            await _too_many(send, wait)
            return

        async def counted(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                self.failures.record(client)
            await send(message)

        await self.app(scope, receive, counted)


async def _too_many(send, wait: int) -> None:
    body = json.dumps({"error": "Too many requests. Try again later."}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(wait).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
