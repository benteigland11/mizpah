"""Demonstrate HTTPClient against a throwaway local echo server.

Uses stdlib http.server so it runs with zero network access. No real API
calls, no credentials. The server binds to 127.0.0.1 on an ephemeral port
and is shut down at the end.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.urllib_client import HTTPClient


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _json_response(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/widgets/demo":
            self._json_response(200, {"id": "demo", "version": "1.0.0"})
        else:
            self._json_response(404, {"detail": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body_bytes = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Type", "").startswith("multipart/"):
            # Just confirm receipt of multipart payload
            self._json_response(201, {"uploaded_bytes": len(body_bytes)})
        else:
            sent = json.loads(body_bytes or b"{}")
            self._json_response(201, {"echo": sent})


server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
host, port = server.server_address
base = f"http://{host}:{port}"
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    client = HTTPClient(
        base_url=base,
        auth_headers=lambda url: {"Authorization": "Bearer demo-token"},
        user_agent="example-usage/0.1",
    )

    found = client.get("/v1/widgets/demo")
    print("GET happy path:", found)

    missing = client.get("/v1/widgets/nope")
    print("GET 404:", missing)

    created = client.post("/v1/widgets", data={"name": "example"})
    print("POST echo:", created)

    upload = client.post_multipart(
        "/v1/upload",
        fields={"widget_id": "demo"},
        file_data=b"ZIPBYTES",
        filename="demo.zip",
        file_content_type="application/zip",
    )
    print("POST multipart:", upload)

    unreachable = HTTPClient(base_url="http://127.0.0.1:1", default_timeout=0.5)
    print("unreachable:", unreachable.get("/v1/thing"))
finally:
    server.shutdown()
    server.server_close()
