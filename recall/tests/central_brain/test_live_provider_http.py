from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

RECALL = Path(__file__).resolve().parents[2]
SERVER = RECALL / "server"
sys.path.insert(0, str(SERVER))

from recall_server.live_providers import (  # noqa: E402
    LiveProviderError,
    planetscale_provider,
    render_provider,
)


class FakeResponse:
    def __init__(self, status: int, payload: bytes):
        self.status = status
        self.payload = io.BytesIO(payload)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.payload.read(size)

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response


class ProviderHttpTest(unittest.TestCase):
    def test_render_request_is_exact_https_bearer_json(self):
        response = FakeResponse(200, b'[{"service":{"id":"synthetic"}}]')
        opener = FakeOpener(response)
        provider = render_provider("synthetic-render-key", opener=opener)
        status, body = provider.request("GET", "/services?ownerId=synthetic&limit=20")
        self.assertEqual(status, 200)
        self.assertEqual(body[0]["service"]["id"], "synthetic")
        request, timeout = opener.calls[0]
        self.assertEqual(
            request.full_url,
            "https://api.render.com/v1/services?ownerId=synthetic&limit=20",
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer synthetic-render-key",
        )
        self.assertLessEqual(timeout, 60)
        self.assertTrue(response.closed)

    def test_planetscale_request_uses_documented_token_pair(self):
        response = FakeResponse(201, b'{"id":"synthetic-db"}')
        opener = FakeOpener(response)
        provider = planetscale_provider(
            "synthetic-token-id", "synthetic-token", opener=opener
        )
        status, _ = provider.request(
            "POST",
            "/organizations/synthetic/databases",
            {"name": "synthetic"},
        )
        self.assertEqual(status, 201)
        request, _ = opener.calls[0]
        self.assertEqual(
            request.get_header("Authorization"),
            "synthetic-token-id:synthetic-token",
        )
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data),
            {"name": "synthetic"},
        )

    def test_transport_rejects_ssrf_redirects_and_bad_json(self):
        provider = render_provider(
            "synthetic", opener=FakeOpener(FakeResponse(200, b"{}"))
        )
        for path in (
            "https://attacker.invalid/",
            "//attacker.invalid/",
            "/services#fragment",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    LiveProviderError, "provider_request_invalid"
                ):
                    provider.request("GET", path)
        duplicate = render_provider(
            "synthetic",
            opener=FakeOpener(FakeResponse(200, b'{"id":"a","id":"b"}')),
        )
        with self.assertRaisesRegex(LiveProviderError, "provider_response_invalid"):
            duplicate.request("GET", "/services")

    def test_provider_errors_are_content_free(self):
        secret = "synthetic-secret-that-must-not-render"

        class FailingOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError(secret)

        provider = render_provider(secret, opener=FailingOpener())
        with self.assertRaises(LiveProviderError) as raised:
            provider.request("GET", "/services")
        self.assertEqual(str(raised.exception), "provider_transport_failed")
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
