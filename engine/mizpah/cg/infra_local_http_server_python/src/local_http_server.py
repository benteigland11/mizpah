"""Loopback-bound HTTP server lifecycle for local-first applications.

Wraps the standard library's threading HTTP server with the three things a
local desktop-style app always needs and usually gets wrong:

* **Loopback by default.** Binding a local app to a wildcard address exposes a
  single-user application, with no authentication, to the whole network.
  Non-loopback hosts are refused unless the caller passes an explicit
  ``allow_remote``, so the dangerous case is never the accidental one.
  The default host is the ``localhost`` name rather than an address literal;
  pass an explicit loopback address when a specific address family matters.
* **A port you can actually learn.** Port ``0`` asks the OS for any free
  port; the bound port is readable afterwards, so the caller can print or
  open the real URL.
* **A shutdown that returns.** The server runs on a daemon thread and
  ``shutdown`` joins it, so callers are not left with a hung process or a
  socket held open until interpreter exit.

The request handler is injected, so this widget owns lifecycle only and never
decides how a request is answered.
"""

from __future__ import annotations

import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from socketserver import BaseServer
from types import TracebackType
from typing import Callable

DEFAULT_HOST = "localhost"
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
LOOPBACK_HOSTNAMES = frozenset({"localhost", ""})

ServerFactory = Callable[[tuple[str, int], type[BaseHTTPRequestHandler]], HTTPServer]


class RemoteBindRefused(RuntimeError):
    """Raised when a non-loopback bind is requested without explicit opt-in."""


class ServerNotRunning(RuntimeError):
    """Raised when an operation requires a started server and none is running."""


def is_loopback_host(host: str) -> bool:
    """True when ``host`` can only be reached from this machine.

    Accepts loopback literals in either address family as well as the
    ``localhost`` hostname and the empty string, which some callers use to
    mean "the default local interface".
    """
    normalized = host.strip().lower()
    if normalized in LOOPBACK_HOSTNAMES:
        return True
    stripped = normalized.strip("[]")
    try:
        return ipaddress.ip_address(stripped).is_loopback
    except ValueError:
        return False


class LocalHttpServer:
    """A loopback HTTP server with an explicit, joinable lifecycle.

    ``handler_class`` is any ``BaseHTTPRequestHandler`` subclass. Nothing about
    request handling is decided here.

    Use it as a context manager for scoped serving, or call ``start`` and
    ``shutdown`` directly when the lifetime is owned elsewhere. ``serve_forever``
    blocks the calling thread instead, for a foreground process.
    """

    def __init__(
        self,
        handler_class: type[BaseHTTPRequestHandler],
        host: str = DEFAULT_HOST,
        port: int = 0,
        allow_remote: bool = False,
        server_factory: ServerFactory | None = None,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if not allow_remote and not is_loopback_host(host):
            raise RemoteBindRefused(
                f"refusing to bind {host!r}: this would expose the application beyond "
                f"this machine. Pass allow_remote=True if that is genuinely intended."
            )
        self._handler_class = handler_class
        self._requested_host = host
        self._requested_port = port
        self._shutdown_timeout = shutdown_timeout
        self._server_factory: ServerFactory = server_factory or _default_server_factory
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._serving = False

    @property
    def running(self) -> bool:
        """True between a successful bind and a completed shutdown."""
        return self._server is not None

    @property
    def host(self) -> str:
        """The host actually bound. Raises if the server is not bound."""
        return self._require_server().server_address[0]

    @property
    def port(self) -> int:
        """The port actually bound, resolved even when port 0 was requested."""
        return self._require_server().server_address[1]

    @property
    def url(self) -> str:
        """The base URL a browser should open to reach this server."""
        host = self.host
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    def bind(self) -> None:
        """Claim the socket without serving yet.

        Separated from ``start`` so a caller can learn the port, or fail fast
        on a port conflict, before spawning the serving thread.
        """
        if self._server is not None:
            return
        self._server = self._server_factory(
            (self._requested_host, self._requested_port), self._handler_class
        )

    def start(self) -> None:
        """Bind if needed and serve on a background daemon thread."""
        self.bind()
        if self._thread is not None:
            return
        server = self._require_server()
        thread = threading.Thread(
            target=self._serve,
            name=f"local-http-server-{self.port}",
            daemon=True,
        )
        self._thread = thread
        self._serving = True
        thread.start()

    def serve_forever(self) -> None:
        """Bind if needed and serve on the calling thread until shutdown."""
        self.bind()
        self._serving = True
        self._serve()

    def _serve(self) -> None:
        try:
            self._require_server().serve_forever()
        finally:
            self._serving = False

    def shutdown(self) -> None:
        """Stop serving, join the thread, and release the socket.

        Safe to call more than once, and safe to call on a server that was
        bound but never served. That second case matters: the standard
        library's ``shutdown`` waits on an event that only ``serve_forever``
        ever sets, so asking a merely-bound server to shut down would block
        forever. Here it just closes the socket.
        """
        server = self._server
        if server is None:
            return
        if self._serving:
            server.shutdown()
            self._serving = False
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._shutdown_timeout)
        server.server_close()
        self._server = None

    def _require_server(self) -> HTTPServer:
        if self._server is None:
            raise ServerNotRunning("server is not bound; call bind() or start() first")
        return self._server

    def __enter__(self) -> LocalHttpServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Threading server that does not strand a port in TIME_WAIT on restart."""

    allow_reuse_address = True
    daemon_threads = True


def _default_server_factory(
    address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]
) -> HTTPServer:
    return _ReusableThreadingHTTPServer(address, handler_class)


def server_address_of(server: BaseServer) -> tuple[str, int]:
    """Read a bound ``(host, port)`` from any socket server.

    Useful when a caller supplies its own ``server_factory`` and wants the
    same address handling without depending on the concrete server type.
    """
    address = server.server_address
    return (str(address[0]), int(address[1]))
