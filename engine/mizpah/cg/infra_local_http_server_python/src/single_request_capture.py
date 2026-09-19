"""Capture exactly one request on a loopback server, then stop.

The shape every browser-redirect handshake needs: bind a loopback port, hand
the browser a URL, wait for it to come back once with a query string, answer
with a small page, and release the port. Nothing here knows what the query
means; the caller parses it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler
from typing import Callable

from .local_http_server import DEFAULT_HOST, LocalHttpServer

DEFAULT_CAPTURE_TIMEOUT = 300.0
DEFAULT_RESPONSE_HTML = "<!doctype html><title>Done</title><p>You can close this window.</p>"
DEFAULT_MISS_HTML = "<!doctype html><title>Not found</title><p>Nothing here.</p>"


class CaptureTimeout(TimeoutError):
    """Raised when no matching request arrived within the wait budget."""


class SingleRequestCapture:
    """Serve one matching ``GET`` and remember its request target.

    ``path`` is the exact path the request must hit (``/auth/callback``);
    anything else gets ``miss_html`` and a 404 without ending the capture.
    ``response_html`` is what the browser shows once the request lands. The
    captured value is the request target as sent (path plus query string),
    so the caller can rebuild the full URL with ``url`` if it wants one.
    """

    def __init__(
        self,
        path: str,
        host: str = DEFAULT_HOST,
        port: int = 0,
        response_html: str = DEFAULT_RESPONSE_HTML,
        miss_html: str = DEFAULT_MISS_HTML,
        on_request: Callable[[str], None] | None = None,
    ) -> None:
        if not path.startswith("/"):
            raise ValueError("path must begin with a slash")
        self._path = path
        self._response_html = response_html
        self._miss_html = miss_html
        self._on_request = on_request
        self._captured: str | None = None
        self._event = threading.Event()
        self._server = LocalHttpServer(self._handler_class(), host=host, port=port)

    @property
    def url(self) -> str:
        """The loopback URL the browser is sent back to, including ``path``."""
        return self._server.url + self._path

    @property
    def captured(self) -> str | None:
        """The request target seen, or ``None`` until one lands."""
        return self._captured

    def start(self) -> None:
        self._server.start()

    def wait(self, timeout: float = DEFAULT_CAPTURE_TIMEOUT) -> str:
        """Block until the matching request arrives; return its request target."""
        if not self._event.wait(timeout):
            raise CaptureTimeout(f"no request on {self._path} within {timeout:g}s")
        assert self._captured is not None
        return self._captured

    def shutdown(self) -> None:
        self._server.shutdown()

    def __enter__(self) -> SingleRequestCapture:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                target = self.path
                if target.split("?", 1)[0] != capture._path:
                    _reply(self, 404, capture._miss_html)
                    return
                if not capture._event.is_set():
                    capture._captured = target
                    if capture._on_request is not None:
                        capture._on_request(target)
                    capture._event.set()
                _reply(self, 200, capture._response_html)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


def _reply(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)
