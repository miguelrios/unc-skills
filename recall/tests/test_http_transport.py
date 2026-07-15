from __future__ import annotations

import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from privacy.transport import open_no_redirect


class _SinkHandler(BaseHTTPRequestHandler):
    requests = 0

    def do_GET(self) -> None:
        type(self).requests += 1
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args) -> None:
        return


class _RedirectHandler(BaseHTTPRequestHandler):
    destination = ""

    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", type(self).destination)
        self.end_headers()

    def log_message(self, _format: str, *_args) -> None:
        return


class AuthenticatedTransportTest(unittest.TestCase):
    def test_redirect_is_rejected_before_authorization_can_reach_destination(self) -> None:
        sink = ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
        redirect = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        _SinkHandler.requests = 0
        _RedirectHandler.destination = f"http://127.0.0.1:{sink.server_port}/capture"
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (sink, redirect)
        ]
        for thread in threads:
            thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{redirect.server_port}/start",
                headers={"Authorization": "Bearer synthetic-secret"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                open_no_redirect(request, timeout=2)
            self.assertEqual(raised.exception.code, 302)
            self.assertEqual(_SinkHandler.requests, 0)
        finally:
            redirect.shutdown()
            sink.shutdown()
            redirect.server_close()
            sink.server_close()


if __name__ == "__main__":
    unittest.main()
