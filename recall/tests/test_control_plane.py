import base64
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

from server.recall_server.control import (
    ComposioConnectionBroker,
    ControlError,
    ControlPlane,
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
            with self.assertRaisesRegex(ControlError, "control_encryption_key_invalid"):
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
        with self.assertRaisesRegex(ControlError, "google_redirect_uri_invalid"):
            GoogleOAuthProvider(
                client_id="synthetic-client",
                client_secret="synthetic-secret",
                redirect_uri="http://recall.example/callback",
            )


class FakeConnectedAccounts:
    def __init__(self, account):
        self.account = account
        self.get_calls = []
        self.delete_calls = []

    def get(self, account_id):
        self.get_calls.append(account_id)
        return self.account

    def delete(self, account_id, **kwargs):
        self.delete_calls.append((account_id, kwargs))
        return SimpleNamespace(success=True)


class FakeComposioSession:
    def __init__(self):
        self.authorize_calls = []

    def authorize(self, toolkit, **kwargs):
        self.authorize_calls.append((toolkit, kwargs))
        return SimpleNamespace(
            id="ca_synthetic_account_123",
            redirect_url="https://connect.composio.dev/link/synthetic",
        )


class FakeComposioClient:
    def __init__(self, account):
        self.session = FakeComposioSession()
        self.connected_accounts = FakeConnectedAccounts(account)
        self.create_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.session


class FakeProbeRail:
    should_fail = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def run(self, operation, params):
        self.calls.append((operation, params))
        if self.should_fail:
            raise RuntimeError("synthetic capability rejection")
        return {"historyId": "synthetic"}


class ComposioConnectionBrokerTests(unittest.TestCase):
    def account(self, **changes):
        values = {
            "id": "ca_synthetic_account_123",
            "user_id": "principal:synthetic:owner",
            "status": "ACTIVE",
            "toolkit": SimpleNamespace(slug="gmail"),
            "requested_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def broker(self, account=None):
        client = FakeComposioClient(account or self.account())
        rails = []

        def rail_factory(**kwargs):
            rail = FakeProbeRail(**kwargs)
            rails.append(rail)
            return rail

        broker = ComposioConnectionBroker(
            api_key="synthetic-project-authority",
            redirect_uri="https://recall.example/admin/oauth/callback/composio",
            auth_configs={"gmail": "ac_synthetic_gmail"},
            client_factory=lambda _key: client,
            rail_factory=rail_factory,
        )
        return broker, client, rails

    def test_connect_link_is_user_toolkit_state_and_explicit_account_bound(self):
        broker, client, _rails = self.broker()
        started = broker.start_connection(
            user_id="principal:synthetic:owner",
            connector_id="google.gmail",
            state="s" * 48,
        )
        self.assertEqual(started.connected_account_id, "ca_synthetic_account_123")
        callback = parse_qs(
            urlsplit(client.session.authorize_calls[0][1]["callback_url"]).query
        )
        self.assertEqual(callback, {"state": ["s" * 48]})
        self.assertEqual(
            client.create_calls,
            [
                {
                    "user_id": "principal:synthetic:owner",
                    "toolkits": ["gmail"],
                    "manage_connections": False,
                    "multi_account": {
                        "enable": True,
                        "require_explicit_selection": True,
                    },
                    "auth_configs": {"gmail": "ac_synthetic_gmail"},
                }
            ],
        )

    def test_completion_verifies_account_owner_toolkit_scope_and_capability(self):
        broker, client, rails = self.broker()
        tokens = broker.complete_connection(
            user_id="principal:synthetic:owner",
            connector_id="google.gmail",
            expected_connected_account_id="ca_synthetic_account_123",
            callback_connected_account_id="ca_synthetic_account_123",
            callback_status="success",
            required_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        )
        self.assertEqual(tokens.subject_id, "gmail:ca_synthetic_account_123")
        self.assertEqual(
            tokens.credentials,
            {
                "user_id": "principal:synthetic:owner",
                "connected_account_id": "ca_synthetic_account_123",
                "toolkit": "gmail",
            },
        )
        self.assertEqual(
            client.connected_accounts.get_calls, ["ca_synthetic_account_123"]
        )
        self.assertEqual(rails[0].calls, [("gmail.users.getProfile", {"userId": "me"})])

    def test_callback_replay_or_cross_user_account_fails_before_probe(self):
        cases = [
            ({}, "ca_different_account_456", "oauth_connection_mismatch"),
            (
                {"user_id": "principal:other:user"},
                "ca_synthetic_account_123",
                "oauth_connection_forbidden",
            ),
            (
                {"status": "EXPIRED"},
                "ca_synthetic_account_123",
                "oauth_connection_inactive",
            ),
            (
                {"toolkit": SimpleNamespace(slug="googlecalendar")},
                "ca_synthetic_account_123",
                "oauth_connection_mismatch",
            ),
            (
                {"requested_scopes": ["scope:other"]},
                "ca_synthetic_account_123",
                "oauth_scope_insufficient",
            ),
        ]
        for account_changes, callback_id, code in cases:
            with self.subTest(code=code):
                broker, _client, rails = self.broker(self.account(**account_changes))
                with self.assertRaisesRegex(ControlError, code):
                    broker.complete_connection(
                        user_id="principal:synthetic:owner",
                        connector_id="google.gmail",
                        expected_connected_account_id="ca_synthetic_account_123",
                        callback_connected_account_id=callback_id,
                        callback_status="success",
                        required_scopes=(
                            "https://www.googleapis.com/auth/gmail.readonly",
                        ),
                    )
                self.assertEqual(rails, [])

    def test_revoke_rechecks_owner_toolkit_then_revokes_upstream(self):
        broker, client, _rails = self.broker()
        broker.revoke(
            {
                "user_id": "principal:synthetic:owner",
                "connected_account_id": "ca_synthetic_account_123",
                "toolkit": "gmail",
            }
        )
        self.assertEqual(
            client.connected_accounts.delete_calls,
            [("ca_synthetic_account_123", {"revoke_on_delete": True})],
        )

    def test_completion_requires_a_live_capability_probe(self):
        broker, _client, rails = self.broker()
        original = FakeProbeRail.should_fail
        FakeProbeRail.should_fail = True
        try:
            with self.assertRaisesRegex(ControlError, "oauth_scope_insufficient"):
                broker.complete_connection(
                    user_id="principal:synthetic:owner",
                    connector_id="google.gmail",
                    expected_connected_account_id="ca_synthetic_account_123",
                    callback_connected_account_id="ca_synthetic_account_123",
                    callback_status="success",
                    required_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
                )
        finally:
            FakeProbeRail.should_fail = original
        self.assertEqual(len(rails), 1)


class AdminProfileTests(unittest.TestCase):
    def test_public_admin_fails_closed_without_every_security_boundary(self):
        incomplete = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
        }
        with patch.dict(os.environ, incomplete, clear=True):
            with self.assertRaisesRegex(RuntimeError, "admin web requires auth"):
                validate_http_profile()

    def test_public_admin_accepts_complete_secret_references(self):
        complete = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
            .rstrip(b"=")
            .decode(),
            "RECALL_GOOGLE_CLIENT_ID": "synthetic-client",
            "RECALL_GOOGLE_CLIENT_SECRET": "synthetic-secret",
            "RECALL_GOOGLE_REDIRECT_URI": (
                "https://recall.example/admin/oauth/callback/google"
            ),
        }
        with patch.dict(os.environ, complete, clear=True):
            validate_http_profile()

    def test_public_admin_operates_without_an_oauth_provider(self):
        local_only = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
            .rstrip(b"=")
            .decode(),
        }
        with patch.dict(os.environ, local_only, clear=True):
            validate_http_profile()
            self.assertEqual(ControlPlane.from_env(object()).providers, {})

    def test_partial_google_configuration_fails_closed(self):
        partial = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
            .rstrip(b"=")
            .decode(),
            "RECALL_GOOGLE_CLIENT_ID": "synthetic-client",
        }
        with patch.dict(os.environ, partial, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be complete"):
                validate_http_profile()

    def test_partial_composio_configuration_fails_closed(self):
        partial = {
            "RECALL_ADMIN_WEB_ENABLED": "1",
            "RECALL_AUTH_REQUIRED": "1",
            "RECALL_HTTP_PROFILE": "public-mcp",
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
            .rstrip(b"=")
            .decode(),
            "RECALL_COMPOSIO_API_KEY": "synthetic-project-authority",
        }
        with patch.dict(os.environ, partial, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be complete"):
                validate_http_profile()

    def test_complete_composio_configuration_registers_broker(self):
        complete = {
            "RECALL_CONTROL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
            .rstrip(b"=")
            .decode(),
            "RECALL_COMPOSIO_API_KEY": "synthetic-project-authority",
            "RECALL_COMPOSIO_REDIRECT_URI": (
                "https://recall.example/admin/oauth/callback/composio"
            ),
        }
        with patch.dict(os.environ, complete, clear=True):
            plane = ControlPlane.from_env(object())
            self.assertIsInstance(plane.providers["composio"], ComposioConnectionBroker)

    def test_device_route_rejects_non_scalar_identifiers_before_storage(self):
        plane = ControlPlane(object(), object(), {})
        with self.assertRaisesRegex(ControlError, "device_route_invalid"):
            plane.create_device_installation(
                principal_id="principal:owner",
                connector_id=["local.codex"],
                tenant_id="tenant:personal",
                device_id="mac-synthetic",
                source_id="codex:synthetic",
                privacy_mode="scrub",
                selectors={},
            )


if __name__ == "__main__":
    unittest.main()
