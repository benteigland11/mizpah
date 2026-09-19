"""The one door out of a private network namespace: an HTTP proxy on a unix socket, with an allowlist.

Commands and services in the sandbox have no route to anything but loopback. Inside the namespace a bridge
listens on 127.0.0.1:<port> and forwards to this proxy's unix socket on the host. The proxy speaks plain
HTTP (absolute-URI requests) and CONNECT (TLS tunnels), decides by host name against an allowlist, and
writes one line per decision to an egress log. Content is never inspected; a domain is the unit of policy.
Local and private addresses are refused whatever the allowlist says: the namespace exists so that the
host's own services are out of reach.

Run as a module: `python -m egress_proxy <socket-path> <log-path> [domain ...]`; the process serves until
killed. Stdlib only.
"""
from __future__ import annotations

import ipaddress
import json
import os
import select
import socket
import socketserver
import sys
import threading
import time
from typing import Any

PRIVATE_NAMES = {'localhost', 'localhost.localdomain', 'ip6-localhost'}


def host_allowed(host: str, allowed: tuple[str, ...]) -> tuple[bool, str]:
    """A host passes when it is (a subdomain of) an allowed domain and is not a local or private address."""
    name = host.strip().strip('[]').lower().rstrip('.')
    if not name or name in PRIVATE_NAMES or name.endswith('.local') or name.endswith('.localhost'):
        return False, 'local name'
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        address = None
    if address is not None:
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
            return False, 'private address'
        return any(name == a for a in allowed), 'ip literal'
    for domain in allowed:
        domain = domain.lower().lstrip('.')
        if name == domain or name.endswith('.'+domain):
            return True, 'allowed'
    return False, 'not in allowlist'


def _parse_target(request_line: str, headers: dict[str, str]) -> tuple[str, int, bool]:
    method, _, rest = request_line.partition(' ')
    target = rest.rsplit(' ', 1)[0]
    if method.upper() == 'CONNECT':
        host, _, port = target.rpartition(':')
        return host, int(port or 443), True
    if '://' in target:
        after = target.split('://', 1)[1]
        host_port = after.split('/', 1)[0]
        host, _, port = host_port.rpartition(':') if ':' in host_port and not host_port.endswith(']') else (host_port, '', '')
        return host or host_port, int(port or 80), False
    host_port = headers.get('host', '')
    host, _, port = host_port.rpartition(':') if ':' in host_port else (host_port, '', '')
    return host or host_port, int(port or 80), False


class Proxy(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, log_path: str, allowed: tuple[str, ...], *, connect_timeout: float = 20.0) -> None:
        self.allowed = tuple(allowed)
        self.log_path = log_path
        self.connect_timeout = connect_timeout
        self.lock = threading.Lock()
        super().__init__(path, Handler)

    def log(self, **fields: Any) -> None:
        with self.lock, open(self.log_path, 'a') as handle:
            handle.write(json.dumps(dict(at=time.time(), **fields))+'\n')


class Handler(socketserver.StreamRequestHandler):
    server: Proxy

    def handle(self) -> None:
        try:
            request_line = self.rfile.readline(8192).decode('latin-1').rstrip('\r\n')
            headers: dict[str, str] = {}
            raw_headers: list[bytes] = []
            while True:
                line = self.rfile.readline(8192)
                if line in (b'\r\n', b'\n', b''):
                    break
                raw_headers.append(line)
                key, _, value = line.decode('latin-1').partition(':')
                headers[key.strip().lower()] = value.strip()
            if not request_line:
                return
            host, port, tunnel = _parse_target(request_line, headers)
        except (ValueError, UnicodeDecodeError):
            self.wfile.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
            return
        ok, why = host_allowed(host, self.server.allowed)
        self.server.log(host=host, port=port, tunnel=tunnel, allowed=ok, why=why)
        if not ok:
            body = ('egress refused: '+host+' ('+why+'). Allowed domains: '+', '.join(self.server.allowed)+'\n').encode()
            self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: '+str(len(body)).encode()
                             +b'\r\nConnection: close\r\n\r\n'+body)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=self.server.connect_timeout)
        except OSError as error:
            body = ('upstream unreachable: '+str(error)+'\n').encode()
            self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
            return
        with upstream:
            upstream.settimeout(None)
            if tunnel:
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                self.wfile.flush()
            else:
                # Forward the request with an origin-form target and hop-by-hop headers dropped.
                method, _, rest = request_line.partition(' ')
                target, _, version = rest.rpartition(' ')
                path = '/'+target.split('://', 1)[1].split('/', 1)[1] if '://' in target and '/' in target.split('://', 1)[1] else ('/' if '://' in target else target)
                forwarded = [(method+' '+path+' '+version).encode('latin-1')+b'\r\n']
                forwarded += [h for h in raw_headers if not h.lower().startswith((b'proxy-', b'connection:'))]
                forwarded.append(b'Connection: close\r\n\r\n')
                upstream.sendall(b''.join(forwarded))
                length = int(headers.get('content-length') or 0)
                if length:
                    upstream.sendall(self.rfile.read(length))
            self._pump(self.connection, upstream)

    @staticmethod
    def _pump(client: socket.socket, upstream: socket.socket) -> None:
        pairs = {client: upstream, upstream: client}
        open_sockets = [client, upstream]
        while len(open_sockets) == 2:
            readable, _, _ = select.select(open_sockets, [], [], 300)
            if not readable:
                break
            for source in readable:
                try:
                    data = source.recv(65536)
                except OSError:
                    data = b''
                if not data:
                    open_sockets = []
                    break
                try:
                    pairs[source].sendall(data)
                except OSError:
                    open_sockets = []
                    break


def serve(path: str, log_path: str, allowed: tuple[str, ...]) -> None:
    if os.path.exists(path):
        os.unlink(path)
    server = Proxy(path, log_path, allowed)
    os.chmod(path, 0o600)
    server.serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    serve(sys.argv[1], sys.argv[2], tuple(sys.argv[3:]))
