"""Tests for the loopback HTTP server lifecycle."""

import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.local_http_server import (  # noqa: E402
    LocalHttpServer,
    RemoteBindRefused,
    ServerNotRunning,
    is_loopback_host,
    server_address_of,
)


class EchoHandler(BaseHTTPRequestHandler):
    """Minimal handler that reports the path it was asked for."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming contract
        body = f"ok:{self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log during tests."""


def fetch(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.5", "localhost", "LOCALHOST", "::1", "[::1]", ""],
)
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.invalid", "::"])
def test_non_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback_host(host) is False


def test_remote_bind_is_refused_by_default() -> None:
    with pytest.raises(RemoteBindRefused):
        LocalHttpServer(EchoHandler, host="0.0.0.0")


def test_remote_bind_is_allowed_with_explicit_opt_in() -> None:
    server = LocalHttpServer(EchoHandler, host="0.0.0.0", allow_remote=True)
    assert server.running is False


def test_ephemeral_port_is_resolved_after_bind() -> None:
    server = LocalHttpServer(EchoHandler, port=0)
    server.bind()
    try:
        assert server.port > 0
        assert is_loopback_host(server.host)
    finally:
        server.shutdown()


def test_url_reflects_the_bound_address() -> None:
    server = LocalHttpServer(EchoHandler)
    server.bind()
    try:
        assert server.url == f"http://{server.host}:{server.port}"
    finally:
        server.shutdown()


def test_ipv6_url_is_bracketed() -> None:
    server = LocalHttpServer(EchoHandler, host="::1")
    try:
        server.bind()
    except OSError:
        pytest.skip("no IPv6 loopback available in this environment")
    try:
        assert server.url == f"http://[::1]:{server.port}"
    finally:
        server.shutdown()


def test_server_answers_requests_while_started() -> None:
    with LocalHttpServer(EchoHandler) as server:
        assert fetch(f"{server.url}/hello") == "ok:/hello"


def test_context_manager_shuts_down_on_exit() -> None:
    with LocalHttpServer(EchoHandler) as server:
        url = server.url
        assert server.running is True
    assert server.running is False
    with pytest.raises(urllib.error.URLError):
        fetch(f"{url}/hello", timeout=1.0)


def test_running_is_false_before_start() -> None:
    assert LocalHttpServer(EchoHandler).running is False


def test_shutdown_is_idempotent() -> None:
    server = LocalHttpServer(EchoHandler)
    server.start()
    server.shutdown()
    server.shutdown()
    assert server.running is False


def test_shutdown_without_start_is_a_no_op() -> None:
    LocalHttpServer(EchoHandler).shutdown()


def test_shutdown_after_bind_without_serving_returns() -> None:
    """Regression: the stdlib shutdown waits on an event only serve_forever
    sets, so a bound-but-never-served server must not be asked to shut down."""
    server = LocalHttpServer(EchoHandler)
    server.bind()
    server.shutdown()
    assert server.running is False


def test_repeated_start_does_not_spawn_a_second_thread() -> None:
    before = threading.active_count()
    server = LocalHttpServer(EchoHandler)
    try:
        server.start()
        server.start()
        assert threading.active_count() <= before + 1
    finally:
        server.shutdown()


def test_port_before_bind_raises() -> None:
    with pytest.raises(ServerNotRunning):
        _ = LocalHttpServer(EchoHandler).port


def test_host_before_bind_raises() -> None:
    with pytest.raises(ServerNotRunning):
        _ = LocalHttpServer(EchoHandler).host


def test_bind_is_idempotent() -> None:
    server = LocalHttpServer(EchoHandler)
    server.bind()
    try:
        first = server.port
        server.bind()
        assert server.port == first
    finally:
        server.shutdown()


def test_injected_server_factory_is_used() -> None:
    calls: list[tuple[str, int]] = []

    def factory(address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> HTTPServer:
        calls.append(address)
        return HTTPServer(address, handler)

    server = LocalHttpServer(EchoHandler, server_factory=factory)
    server.bind()
    try:
        assert calls == [("localhost", 0)]
    finally:
        server.shutdown()


def test_serve_forever_runs_until_shutdown() -> None:
    server = LocalHttpServer(EchoHandler)
    server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert fetch(f"{server.url}/direct") == "ok:/direct"
    finally:
        server.shutdown()
    thread.join(timeout=5.0)
    assert thread.is_alive() is False


def test_port_is_released_for_rebinding_after_shutdown() -> None:
    first = LocalHttpServer(EchoHandler)
    first.start()
    port = first.port
    first.shutdown()

    second = LocalHttpServer(EchoHandler, port=port)
    try:
        second.start()
        assert fetch(f"{second.url}/again") == "ok:/again"
    finally:
        second.shutdown()


def test_server_address_of_reads_a_bound_address() -> None:
    raw = HTTPServer(("localhost", 0), EchoHandler)
    try:
        host, port = server_address_of(raw)
        assert is_loopback_host(host)
        assert port == raw.server_address[1]
    finally:
        raw.server_close()
