import hmac
import ipaddress
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.client import HTTPSConnection, RemoteDisconnected
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from space import webhook_http as http

URL = "https://receiver.example.net/events"
BODY = b'{"event":"memory.created"}'
NOW = 1_700_000_000


def answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    target = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target


class Response:
    def __init__(self, status=200, retry_after=None):
        self.status, self.retry_after, self.closed = status, retry_after, False

    def getheader(self, name):
        assert name == "Retry-After"
        return self.retry_after

    def read(self, *args):
        raise AssertionError("Response bodies must never be read")

    def close(self):
        self.closed = True


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(addresses=["8.8.8.8"], lookups=[], connections=[], status=200)
    state.retry_after, state.failure = None, None

    def lookup(host, port, **kwargs):
        state.lookups.append((host, port, kwargs))
        if isinstance(state.addresses, Exception):
            raise state.addresses
        return [answer(address) for address in state.addresses]

    class Connection:
        def __init__(self, host, address):
            self.host, self.address, self.closed = host, address, False
            self.requests = []
            self.response = Response(state.status, state.retry_after)
            state.connections.append(self)

        def request(self, method, path, *, body, headers):
            self.requests.append((method, path, body, headers))
            if state.failure:
                raise state.failure

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(http, "_PinnedHTTPSConnection", Connection)
    return state


def test_signature_has_stable_raw_byte_contract_and_constant_time_comparison(monkeypatch):
    expected = "v1=1504f8c83db5b190e62ecc175f61b458d5a466ca74d034f695eecea1a518911c"
    assert http.sign("whsec_test", NOW, BODY) == expected
    calls, compare = [], hmac.compare_digest

    def compared(left, right):
        calls.append((left, right))
        return compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", compared)
    monkeypatch.setattr(http.time, "time", lambda: NOW)
    assert http.verify_signature("whsec_test", NOW, BODY, expected)
    assert calls == [(expected, expected)]
    assert http.sign("whsec_test", NOW, BODY + b"\n") != expected


@pytest.mark.parametrize("change", ["secret", "timestamp", "body", "digest", "version"])
def test_tampering_does_not_verify(change):
    key, timestamp, body = "secret", NOW, BODY
    signature = http.sign(key, timestamp, body)
    if change == "secret":
        key = "other"
    elif change == "timestamp":
        timestamp += 1
    elif change == "body":
        body = b'{"event": "memory.created"}'
    elif change == "digest":
        signature = signature[:-1] + ("1" if signature[-1] == "0" else "0")
    else:
        signature = signature.replace("v1=", "v2=")
    assert not http.verify_signature(key, timestamp, body, signature, now=NOW)


@pytest.mark.parametrize(
    "offset,valid", [(-301, False), (-300, True), (0, True), (300, True), (301, False)]
)
def test_replay_window_rejects_old_and_future_timestamps(offset, valid):
    timestamp = NOW + offset
    signature = http.sign("secret", timestamp, BODY)
    assert http.verify_signature("secret", timestamp, BODY, signature, now=NOW) is valid
    if offset:
        assert not http.verify_signature("secret", timestamp, BODY, signature, now=NOW, tolerance=0)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_replay_tolerance_cannot_disable_window(value):
    signature = http.sign("secret", NOW, BODY)
    assert not http.verify_signature("secret", NOW, BODY, signature, now=NOW, tolerance=value)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("HTTPS://Receiver.Example.NET:443", "https://receiver.example.net/"),
        ("https://receiver.example.net./events", URL),
        ("https://bücher.example.net/café", "https://xn--bcher-kva.example.net/caf%C3%A9"),
        ("https://8.8.8.8/events", "https://8.8.8.8/events"),
        ("https://[2606:4700:4700::1111]:443", "https://[2606:4700:4700::1111]/"),
    ],
)
def test_endpoint_normalization(url, expected):
    assert http.validate_endpoint(url) == expected


def test_public_ipv4_mapped_literal_keeps_its_destination():
    parts = urlsplit(http.validate_endpoint("https://[::ffff:8.8.8.8]"))
    assert ipaddress.ip_address(parts.hostname).ipv4_mapped == ipaddress.IPv4Address("8.8.8.8")
    assert parts.scheme == "https" and parts.port is None and parts.path == "/"


@pytest.mark.parametrize(
    "url",
    [prefix + "//receiver.example.net" for prefix in ("http:", "ftp:", "")]
    + [
        "https://" + authority
        for authority in (
            "user:secret@receiver.example.net @receiver.example.net localhost localhost. "
            "sub.localhost local receiver.local receiver.internal metadata.google.internal "
            "intranet receiver.home.arpa receiver.invalid receiver.example.net:80 "
            "receiver.example.net:444 receiver.example.net: receiver.example.net:secret "
            "127.1 0177.0.0.1 2130706433 0x7f.0.0.1 bad_label.example.net -bad.example.net "
            "[fe80::1%25en0]"
        ).split()
    ]
    + [URL + suffix for suffix in ("?token=secret", "?", "#part", "#", "\\private")]
    + [URL + control for control in " \n\t\x00\x7f\u200b"]
    + ["https://receiver.example.net/" + "a" * 2048],
)
def test_invalid_endpoint_is_rejected_before_any_resolution(url, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Syntactic validation must not use DNS")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(ValueError):
        http.validate_endpoint(url)


UNSAFE = (
    "127.0.0.1 0.0.0.0 10.0.0.1 172.16.0.1 192.168.1.1 169.254.169.254 100.64.0.1 "
    "192.0.2.1 198.51.100.1 203.0.113.1 224.0.0.1 240.0.0.1 255.255.255.255 "
    "::1 :: fe80::1 fc00::1 2001:db8::1 ff02::1 ::ffff:127.0.0.1 ::ffff:10.0.0.1 "
    "::ffff:192.0.2.1 64:ff9b::7f00:1 2002:7f00:1::"
).split()


@pytest.mark.parametrize("address", UNSAFE)
def test_non_public_literal_and_dns_answer_are_rejected(address, transport):
    authority = f"[{address}]" if ":" in address else address
    with pytest.raises(ValueError):
        http.validate_endpoint("https://" + authority)
    transport.addresses = [address]
    with pytest.raises(ValueError):
        http.resolve_endpoint(URL)
    assert http.send(URL, BODY, {}).error_code == "unsafe_address"
    assert not transport.connections


@pytest.mark.parametrize(
    "addresses",
    [
        ["8.8.8.8", "127.0.0.1"],
        ["10.0.0.1", "8.8.8.8"],
        ["2606:4700:4700::1111", "::ffff:127.0.0.1"],
    ],
)
def test_mixed_dns_answers_fail_closed(addresses, transport):
    transport.addresses = addresses
    assert http.send(URL, BODY, {}).error_code == "unsafe_address"
    assert not transport.connections


def test_all_public_dns_answers_are_allowed_but_only_one_is_attempted(transport):
    transport.addresses = ["8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8", "8.8.8.8"]
    assert http.resolve_endpoint(URL) == tuple(
        str(ipaddress.ip_address(value)) for value in transport.addresses[:-1]
    )
    assert http.send(URL, BODY, {}) == http.AttemptResult(200)
    assert len(transport.connections) == 1
    assert transport.connections[0].address == "8.8.8.8"
    assert transport.lookups[-1][0] == "receiver.example.net."


def test_send_rechecks_dns_after_configuration_approval(transport):
    assert http.resolve_endpoint(URL) == ("8.8.8.8",)
    transport.addresses = ["169.254.169.254"]
    assert http.send(URL, BODY, {}) == http.AttemptResult(error_code="unsafe_address")
    assert len(transport.lookups) == 2
    assert not transport.connections


@pytest.mark.parametrize("addresses", [[], socket.gaierror("SECRET resolver message")])
def test_dns_failure_retains_only_fixed_code(addresses, transport):
    transport.addresses = addresses
    result = http.send(URL, BODY, {})
    assert result == http.AttemptResult(error_code="dns_error")
    assert "SECRET" not in repr(result) and not transport.connections


@pytest.mark.parametrize(
    "status,code",
    [
        (200, None),
        (204, None),
        (299, None),
        (301, "redirect"),
        (307, "redirect"),
        (400, "http_4xx"),
        (401, "http_4xx"),
        (429, "rate_limited"),
        (500, "http_5xx"),
        (502, "http_5xx"),
        (503, "http_5xx"),
        (100, "invalid_response"),
    ],
)
def test_response_status_classification_without_body_or_redirect_follow(status, code, transport):
    transport.status = status
    result = http.send(URL, BODY, {"X-Space-Event-ID": "event"})
    assert result == http.AttemptResult(status, code)
    assert len(transport.connections) == 1
    connection = transport.connections[0]
    assert len(connection.requests) == 1 and connection.closed and connection.response.closed


@pytest.mark.parametrize(
    "header,delay",
    [("0", 1), ("12", 12), ("999999", 3600), ("-1", None), ("bad", None), (None, None)],
)
@pytest.mark.parametrize("status", [429, 503])
def test_numeric_retry_after_is_bounded(header, delay, status, transport):
    transport.status, transport.retry_after = status, header
    assert http.send(URL, BODY, {}).retry_after == delay


@pytest.mark.parametrize("offset,delay", [(-60, 1), (60, 60), (7200, 3600)])
def test_date_retry_after_is_bounded(offset, delay, transport, monkeypatch):
    transport.status = 503
    date = datetime.fromtimestamp(NOW, UTC) + timedelta(seconds=offset)
    transport.retry_after = format_datetime(date, usegmt=True)
    monkeypatch.setattr(http.time, "time", lambda: NOW)
    assert http.send(URL, BODY, {}).retry_after == delay


@pytest.mark.parametrize("status", [200, 301, 400, 500])
def test_retry_after_is_ignored_for_other_statuses(status, transport):
    transport.status, transport.retry_after = status, "60"
    assert http.send(URL, BODY, {}).retry_after is None


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("SECRET timeout"), "timeout"),
        (ConnectionResetError("SECRET"), "network_error"),
        (RemoteDisconnected("SECRET response"), "network_error"),
        (ssl.SSLEOFError("SECRET handshake closed"), "network_error"),
        (ssl.SSLZeroReturnError("SECRET TLS closed"), "network_error"),
        (ssl.SSLCertVerificationError("SECRET certificate"), "tls_error"),
        (ssl.SSLError("SECRET TLS"), "tls_error"),
        (ValueError("SECRET header"), "invalid_request"),
    ],
)
def test_network_failure_is_sanitized_and_not_retried(error, code, transport):
    transport.failure = error
    result = http.send(URL, BODY, {})
    assert result == http.AttemptResult(error_code=code)
    assert "SECRET" not in repr(result)
    assert len(transport.connections) == 1 and transport.connections[0].closed


def test_cleanup_closes_both_resources_without_disclosing_close_errors(transport, monkeypatch):
    def close(resource):
        resource.closed = True
        raise OSError("SECRET close error")

    monkeypatch.setattr(Response, "close", close)
    monkeypatch.setattr(http._PinnedHTTPSConnection, "close", close)
    assert http.send(URL, BODY, {}) == http.AttemptResult(200)
    connection = transport.connections[0]
    assert connection.closed and connection.response.closed


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
@pytest.mark.parametrize("fail", [False, True])
def test_direct_tls_connection_pins_ip_checks_hostname_and_ignores_proxy_env(
    address,
    fail,
    monkeypatch,
):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    lookups, created, contexts, tls_calls = [], [], [], []

    def lookup(*args, **kwargs):
        lookups.append(args)
        return [answer(address)]

    class RawSocket:
        def __init__(self, family, kind):
            self.family, self.kind, self.closed, self.sent = family, kind, False, []
            created.append(self)

        def settimeout(self, value):
            self.timeout = value

        def connect(self, destination):
            self.destination = destination
            if fail:
                raise TimeoutError("SECRET connection timeout")

        def sendall(self, value):
            self.sent.append(value)

        def close(self):
            self.closed = True

    context_factory = ssl.create_default_context

    def context():
        actual = context_factory()
        contexts.append(actual)

        def wrap(raw, *, server_hostname):
            tls_calls.append((raw, server_hostname))
            return raw

        monkeypatch.setattr(actual, "wrap_socket", wrap)
        return actual

    response = Response(204)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", RawSocket)
    monkeypatch.setattr(ssl, "create_default_context", context)
    monkeypatch.setattr(HTTPSConnection, "getresponse", lambda self: response)
    result = http.send(
        URL,
        BODY,
        {
            "Host": "private.internal",
            "Content-Type": "text/plain",
            "Content-Length": "999",
            "Transfer-Encoding": "chunked",
            "X-Space-Signature": "v1=test",
        },
    )
    assert result == (http.AttemptResult(error_code="timeout") if fail else http.AttemptResult(204))
    assert len(lookups) == len(created) == len(contexts) == 1
    raw = created[0]
    assert raw.timeout == 10 and raw.closed
    assert raw.destination == ((address, 443, 0, 0) if ":" in address else (address, 443))
    assert contexts[0].check_hostname and contexts[0].verify_mode == ssl.CERT_REQUIRED
    if not fail:
        assert tls_calls == [(raw, "receiver.example.net")]
        wire = b"".join(raw.sent)
        assert b"POST /events HTTP/1.1\r\n" in wire
        assert b"Host: receiver.example.net\r\n" in wire
        assert b"Content-Type: application/json\r\n" in wire
        assert f"Content-Length: {len(BODY)}\r\n".encode() in wire
        assert b"X-Space-Signature: v1=test\r\n" in wire
        assert b"private.internal" not in wire and b"Transfer-Encoding" not in wire
        assert wire.endswith(BODY) and response.closed
    else:
        assert not raw.sent and not tls_calls


def route_pinned_address_to(monkeypatch, listener_address):
    """Parameters: pytest's monkeypatch and a loopback listener address.
    What it does: resolves the receiver to one public IP and maps only that pinned
    destination to the listener, so the real connector and the real TLS library run.
    Output: none; production endpoint policy and connector code are not bypassed.
    """
    original_socket = socket.socket

    class TestSocket(original_socket):
        def connect(self, destination):
            assert destination == ("8.8.8.8", 443)
            return super().connect(listener_address)

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [answer("8.8.8.8")])
    monkeypatch.setattr(socket, "socket", TestSocket)


def test_peer_closing_a_real_tls_handshake_is_a_retryable_network_error(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def close_after_client_hello():
        peer, _ = listener.accept()
        peer.recv(4096)
        peer.close()

    thread = threading.Thread(target=close_after_client_hello, daemon=True)
    thread.start()
    route_pinned_address_to(monkeypatch, listener.getsockname())
    try:
        result = http.send(URL, BODY, {})
    finally:
        thread.join(timeout=5)
        listener.close()
    assert result == http.AttemptResult(error_code="network_error")


def test_real_certificate_for_another_host_stays_a_final_tls_error(monkeypatch, tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "other.example.net")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("other.example.net")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "other.pem", tmp_path / "other-key.pem"
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve_other_host_certificate():
        peer, _ = listener.accept()
        try:
            server_context.wrap_socket(peer, server_side=True).close()
        except (ssl.SSLError, OSError):
            peer.close()

    thread = threading.Thread(target=serve_other_host_certificate, daemon=True)
    thread.start()
    original_context = ssl.create_default_context

    def trust_test_certificate():
        context = original_context()
        context.load_verify_locations(cadata=pem.decode("ascii"))
        return context

    monkeypatch.setattr(ssl, "create_default_context", trust_test_certificate)
    route_pinned_address_to(monkeypatch, listener.getsockname())
    try:
        result = http.send(URL, BODY, {})
    finally:
        thread.join(timeout=5)
        listener.close()
    assert urlsplit(URL).hostname != "other.example.net"
    assert result == http.AttemptResult(error_code="tls_error")
