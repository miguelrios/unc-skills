import base64
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from server.recall_server.control import (
    ControlError,
    GoogleOAuthProvider,
    SecretBox,
)
from server.recall_server.app import validate_http_profile


class SecretBoxTests(unittest.TestCase):
    def test_roundtrip_is_purpose_bound_and_not_plaintext(self):
        box = SecretBox(b"k" * 32)
        value = {"refresh_token": "synthetic-private-value"}
        sealed = box.seal(value, purpose="connection:one")
        self.assertNotIn(b"synthetic-private-value", sealed)
        self.assertEqual(
            box.open(sealed, purpose="connection:one"),
            value,
        )
        with self.assertRaisesRegex(ControlError, "control_secret_invalid"):
            box.open(sealed, purpose="connection:two")

    def test_environment_requires_exactly_32_urlsafe_bytes(self):
        encoded = base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode()
        with patch.dict(os.environ, {"RECALL_CONTROL_ENCRYPTION_KEY": encoded}):
            self.assertEqual(SecretBox.from_env().key_id, SecretBox(b"k" * 32).key_id)
        with patch.dict(os.environ, {"RECALL_CONTROL_ENCRYPTION_KEY": "short"}):
            with self.assertRaisesRegex(
                ControlError, "control_encryption_key_invalid"
            ):
                SecretBox.from_env()


class GoogleOAuthProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = GoogleOAuthProvider(
            client_id="synthetic-client",
            client_secret="synthetic-secret",
            redirect_uri="https://recall.example/admin/oauth/callback/google",
        )

    def test_authorization_url_is_offline_incremental_state_and_pkce_bound(self):
        url = self.provider.authorization_url(
            state="s" * 48,
            code_challenge="c" * 43,
            scopes=(
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/gmail.readonly",
            ),
        )
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["state"], ["s" * 48])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], ["c" * 43])

    def test_configuration_rejects_non_https_endpoints(self):
        with self.assertRaisesRegex(
            ControlError, "google_redirect_uri_invalid"
        ):
            GoogleOAuthProvider(
                client_id="synthetic-client",
                client_secret="synthetic-secret",
                redirect_uri="http://recall.example/callback",
            )


class AdminProfileTests(unittest.TestCase):
    def test_public_admin_fails_closed_without_every_security_boundary(self):
        incomplete = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
        }
        with patch.dict(os.environ, incomplete, clear=True):
            with self.assertRaisesRegex(
                RuntimeError, "admin web requires auth"
            ):
                validate_http_profile()

    def test_public_admin_accepts_complete_secret_references(self):
        complete = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(
                b"k" * 32
            ).rstrip(b"=").decode(),
            "RECALL_GOOGLE_CLIENT_ID": "synthetic-client",
            "RECALL_GOOGLE_CLIENT_SECRET": "synthetic-secret",
            "RECALL_GOOGLE_REDIRECT_URI": (
                "https://recall.example/admin/oauth/callback/google"
            ),
        }
        with patch.dict(os.environ, complete, clear=True):
            validate_http_profile()


if __name__ == "__main__":
    unittest.main()
