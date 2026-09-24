"""A reply that starts streaming and then goes silent is cut off after the stall bound, not the socket timeout."""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.subscription_provider_session import urllib_http


class Stalls(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(b'event: response.output_text.delta\ndata: {}\n\n')
        self.wfile.flush()
        time.sleep(6)

    def log_message(self, *args):
        pass


def test_a_reply_that_stops_mid_stream_is_cut_off_after_the_stall_bound():
    server = HTTPServer(('127.0.0.1', 0), Stalls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    call = urllib_http()
    call.stall_seconds = 2
    started = time.monotonic()
    with pytest.raises(OSError, match='stalled'):
        call('POST', f'http://127.0.0.1:{server.server_address[1]}/', {}, b'{}', 60)
    assert time.monotonic()-started < 10
    server.shutdown()
