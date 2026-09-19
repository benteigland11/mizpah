"""Example usage of Local HTTP Server.

Starts a loopback server on an OS-assigned port, makes a couple of requests
against it, demonstrates that a non-loopback bind is refused, and shuts the
server down cleanly. Everything stays on this machine.
"""

import sys
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.local_http_server import LocalHttpServer, RemoteBindRefused, is_loopback_host


class GreetingHandler(BaseHTTPRequestHandler):
    """Answers every GET with a short plain-text greeting."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming contract
        body = f"you asked for {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the example output clean."""


def main() -> None:
    print("A local app should never be reachable from the network by accident:")
    try:
        LocalHttpServer(GreetingHandler, host="0.0.0.0")
    except RemoteBindRefused as error:
        print(f"  refused -> {error}")
    print(f"  is_loopback_host('0.0.0.0') = {is_loopback_host('0.0.0.0')}")
    print(f"  is_loopback_host('127.0.0.1') = {is_loopback_host('127.0.0.1')}\n")

    with LocalHttpServer(GreetingHandler, port=0) as server:
        print(f"serving on {server.url} (port chosen by the OS)")
        for path in ("/", "/spaces/inbox"):
            with urllib.request.urlopen(f"{server.url}{path}", timeout=5) as response:
                print(f"  GET {path:<16} -> {response.status} {response.read().decode()}")

    print("\nshut down cleanly; the socket is released")


main()


# --- One-shot capture: the browser-redirect handshake shape -----------------
from src.single_request_capture import SingleRequestCapture  # noqa: E402

with SingleRequestCapture("/auth/callback", response_html="<p>Signed in.</p>") as capture:
    print(f"send the browser back to: {capture.url}")
    # Stand in for the browser redirect; a real flow appends ?code=...&state=...
    urllib.request.urlopen(capture.url + "?code=demo-code&state=demo-state", timeout=5).read()
    print(f"captured request target: {capture.wait(timeout=5)}")
