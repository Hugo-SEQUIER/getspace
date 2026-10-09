"""Parameters: HTTPS receiver URLs, signing secrets, payload bytes and request headers.
What it does: signs webhooks and sends one public, DNS-validated, IP-pinned HTTPS attempt.
Output: normalized endpoints, signatures and metadata-only attempt results.
"""

import hashlib
import hmac
import http.client
import ipaddress
import math
import re
import socket
import ssl
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

SOCKET_TIMEOUT = 10
_LOCAL_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "private",
    "test",
    "invalid",
    "example",
    "onion",
    "arpa",
    "home.arpa",
)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")


class EndpointError(ValueError):
    def __init__(self, error_code: str):
        """Parameters: a fixed diagnostic code.
        What it does: rejects an endpoint without retaining URLs or resolver errors.
        Output: a ValueError with its safe error_code.
        """
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class AttemptResult:
    status_code: int | None = None
    error_code: str | None = None
    retry_after: int | None = None


def _public_ip(value: str):
    """Parameters: a numeric IPv4 or IPv6 address.
    What it does: rejects non-global, multicast, scoped and unsafe embedded addresses.
    Output: a public address object, or EndpointError.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise EndpointError("unsafe_address") from None
    if "%" in value:
        raise EndpointError("unsafe_address")
    checked = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    checked = checked or address
    if not checked.is_global or checked.is_multicast or checked.is_reserved:
        raise EndpointError("unsafe_address")
    if isinstance(checked, ipaddress.IPv6Address):
        if checked not in _GLOBAL_V6:
            raise EndpointError("unsafe_address")
        if checked.sixtofour is not None:
            _public_ip(str(checked.sixtofour))
    return address


def validate_endpoint(url: str) -> str:
    """Parameters: a receiver URL, no longer than 2048 characters.
    What it does: requires HTTPS, port 443, a public host and an unambiguous URL.
    Output: a normalized ASCII URL, or EndpointError (a ValueError).
    """
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise EndpointError("invalid_endpoint")
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in url):
        raise EndpointError("invalid_endpoint")
    if any(c in url for c in ("\\", "?", "#")):
        raise EndpointError("invalid_endpoint")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
        if parts.scheme != "https" or not host or "@" in parts.netloc:
            raise ValueError
        if port not in (None, 443) or parts.netloc.endswith(":") or "%" in host:
            raise ValueError
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        raise EndpointError("invalid_endpoint") from None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        host = str(_public_ip(host))
        authority = f"[{host}]" if address.version == 6 else host
    else:
        labels = host.split(".")
        if (
            len(host) > 253
            or len(labels) < 2
            or labels[-1].isdigit()
            or not all(_DNS_LABEL.fullmatch(label) for label in labels)
            or any(host == suffix or host.endswith("." + suffix) for suffix in _LOCAL_SUFFIXES)
        ):
            raise EndpointError("invalid_endpoint")
        authority = host
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    normalized = f"https://{authority}{path}"
    if len(normalized) > 2048:
        raise EndpointError("invalid_endpoint")
    return normalized


def resolve_endpoint(url: str) -> tuple[str, ...]:
    """Parameters: a receiver URL.
    What it does: resolves afresh and rejects the entire set if any answer is non-public.
    Output: distinct validated destination IPs, or EndpointError.
    """
    host = urlsplit(validate_endpoint(url)).hostname
    try:
        ipaddress.ip_address(host)
        lookup = host
    except ValueError:
        lookup = host + "."
    try:
        answers = socket.getaddrinfo(
            lookup,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError:
        raise EndpointError("dns_error") from None
    if not answers:
        raise EndpointError("dns_error")
    addresses = []
    for family, socktype, protocol, _, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise EndpointError("unsafe_address")
        if protocol not in (0, socket.IPPROTO_TCP) or sockaddr[1] != 443:
            raise EndpointError("unsafe_address")
        address = _public_ip(sockaddr[0])
        if (address.version == 4) != (family == socket.AF_INET):
            raise EndpointError("unsafe_address")
        if family == socket.AF_INET6 and (sockaddr[2] or sockaddr[3]):
            raise EndpointError("unsafe_address")
        if str(address) not in addresses:
            addresses.append(str(address))
    return tuple(addresses)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """Parameters: a UTF-8 secret, integer Unix timestamp and exact payload bytes.
    What it does: HMAC-SHA256 signs ASCII timestamp, a dot and the unchanged payload.
    Output: v1= followed by the lowercase hexadecimal signature.
    """
    signed = str(timestamp).encode("ascii") + b"." + body
    return "v1=" + hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    now: float | None = None,
    tolerance: int = 300,
) -> bool:
    """Parameters: signing inputs, a signature, optional current time and replay tolerance.
    What it does: bounds timestamp age in both directions and compares in constant time.
    Output: whether the signature and timestamp are valid.
    """
    if type(timestamp) is not int or timestamp < 0 or not isinstance(signature, str):
        return False
    if len(signature) != 67 or not signature.isascii() or not signature.startswith("v1="):
        return False
    try:
        current = time.time() if now is None else now
        if not math.isfinite(current) or not math.isfinite(tolerance) or tolerance < 0:
            return False
        if abs(current - timestamp) > tolerance:
            return False
        return hmac.compare_digest(sign(secret, timestamp, body), signature)
    except (TypeError, ValueError, OverflowError):
        return False


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        """Parameters: the certificate hostname and one validated numeric destination.
        What it does: creates a verified HTTPS connection with a ten-second socket timeout.
        Output: a connection with no proxy, retry or alternate-address behavior.
        """
        super().__init__(
            host, port=443, timeout=SOCKET_TIMEOUT, context=ssl.create_default_context()
        )
        self._address = address

    def connect(self):
        """Parameters: the connection's validated IP and original hostname.
        What it does: connects directly to the IP and verifies TLS for the original host.
        Output: a TLS socket, or a network/TLS exception after closing the raw socket.
        """
        family = socket.AF_INET6 if ":" in self._address else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.timeout)
            destination = (
                (self._address, 443, 0, 0) if family == socket.AF_INET6 else (self._address, 443)
            )
            raw.connect(destination)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _retry_after(value: str | None) -> int | None:
    """Parameters: a Retry-After response header.
    What it does: parses decimal seconds or an HTTP date and clamps the delay.
    Output: 1..3600 seconds, or None for malformed values.
    """
    if not value or len(value) > 128:
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = math.ceil(date.timestamp() - time.time())
        return max(1, min(3600, seconds))
    except (TypeError, ValueError, OverflowError):
        return None


def send(url: str, body: bytes, headers: Mapping[str, str]) -> AttemptResult:
    """Parameters: a receiver URL, exact JSON bytes and webhook headers.
    What it does: revalidates DNS and posts once to a pinned IP without reading the body.
    Output: status, fixed error code and bounded Retry-After only; never response content.
    """
    connection = response = None
    try:
        normalized = validate_endpoint(url)
        addresses = resolve_endpoint(normalized)
        parts = urlsplit(normalized)
        connection = _PinnedHTTPSConnection(parts.hostname, addresses[0])
        reserved = {"host", "content-type", "content-length", "transfer-encoding", "connection"}
        outbound = {key: value for key, value in headers.items() if key.lower() not in reserved}
        outbound.update(
            {
                "Host": parts.netloc,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
            }
        )
        connection.request("POST", parts.path, body=body, headers=outbound)
        response = connection.getresponse()
        status = response.status
        delay = _retry_after(response.getheader("Retry-After")) if status in (429, 503) else None
        if 200 <= status < 300:
            code = None
        elif 300 <= status < 400:
            code = "redirect"
        elif status == 429:
            code = "rate_limited"
        elif 500 <= status < 600:
            code = "http_5xx"
        else:
            code = "http_4xx" if 400 <= status < 500 else "invalid_response"
        return AttemptResult(status, code, delay)
    except EndpointError as error:
        return AttemptResult(error_code=error.error_code)
    except TimeoutError:
        return AttemptResult(error_code="timeout")
    except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
        # The peer closed the connection before TLS completed. That is an interrupted
        # connection, not a failed verification, so it is retried like a connection reset.
        # Every retry performs a new, fully verified handshake.
        return AttemptResult(error_code="network_error")
    except ssl.SSLError:
        return AttemptResult(error_code="tls_error")
    except (OSError, http.client.HTTPException):
        return AttemptResult(error_code="network_error")
    except (ValueError, TypeError):
        return AttemptResult(error_code="invalid_request")
    finally:
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    pass
