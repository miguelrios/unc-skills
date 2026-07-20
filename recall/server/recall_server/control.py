"""Shared, tenant-aware connector control plane for web and native clients."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit
import urllib.error
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from connectors.registry import (
    ConnectorDefinitionV3,
    ConnectorRegistryError,
    REGISTRY,
    definition,
)


ADMIN_TOKEN_RE = re.compile(r"rca_[A-Za-z0-9_-]{32,128}\Z")
SESSION_TOKEN_RE = re.compile(r"rcs_[A-Za-z0-9_-]{32,128}\Z")
STATE_RE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
AUTHORITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/@+-]{1,255}\Z")
DEVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}\Z")
GOOGLE_CONNECTORS = frozenset(
    {"google.gmail", "google.calendar", "google.contacts", "google.drive"}
)
INSTALLATION_STATES = frozenset(
    {"configured", "enabled", "paused", "revoked", "uninstalled"}
)
TRANSITIONS = {
    "enable": ({"configured", "paused"}, "enabled"),
    "pause": ({"enabled"}, "paused"),
    "resume": ({"paused"}, "enabled"),
    "revoke": ({"configured", "enabled", "paused"}, "revoked"),
    "uninstall": ({"configured", "enabled", "paused", "revoked"}, "uninstalled"),
}


class ControlError(RuntimeError):
    """Closed, content-free control-plane failure."""

    def __init__(self, code: str, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(code)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _selector(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int and -(2**31) <= value <= 2**31 - 1:
        return value
    if isinstance(value, str) and value and "\x00" not in value:
        if len(value.encode()) <= 4096:
            return value
    if isinstance(value, list) and len(value) <= 128:
        copied = [_selector(item) for item in value]
        if (
            all(isinstance(item, str) for item in copied)
            and copied == sorted(copied)
            and len(copied) == len(set(copied))
        ):
            return copied
    raise ControlError("oauth_selectors_invalid")


def _b64decode_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise ControlError("control_encryption_key_invalid", 500) from None
    if len(raw) != 32:
        raise ControlError("control_encryption_key_invalid", 500)
    return raw


class SecretBox:
    """Versioned AES-256-GCM envelope with purpose-bound associated data."""

    def __init__(self, key: bytes):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ControlError("control_encryption_key_invalid", 500)
        self._key = key
        self.key_id = hashlib.sha256(key).hexdigest()[:16]

    @classmethod
    def from_env(cls) -> "SecretBox":
        value = os.environ.get("RECALL_CONTROL_ENCRYPTION_KEY", "")
        if not value:
            raise ControlError("control_encryption_key_missing", 500)
        return cls(_b64decode_key(value))

    def seal(self, value: dict[str, Any], *, purpose: str) -> bytes:
        try:
            plaintext = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError):
            raise ControlError("control_secret_invalid") from None
        if len(plaintext) > 64 * 1024:
            raise ControlError("control_secret_too_large")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce, plaintext, purpose.encode()
        )
        return b"\x01" + nonce + ciphertext

    def open(self, envelope: bytes, *, purpose: str) -> dict[str, Any]:
        if (
            not isinstance(envelope, bytes)
            or len(envelope) < 30
            or envelope[:1] != b"\x01"
        ):
            raise ControlError("control_secret_invalid", 500)
        try:
            plaintext = AESGCM(self._key).decrypt(
                envelope[1:13], envelope[13:], purpose.encode()
            )
            value = json.loads(plaintext)
        except Exception:
            raise ControlError("control_secret_invalid", 500) from None
        if not isinstance(value, dict):
            raise ControlError("control_secret_invalid", 500)
        return value


@dataclass(frozen=True)
class OAuthTokens:
    subject_id: str
    granted_scopes: tuple[str, ...]
    credentials: dict[str, Any]
    expires_at: datetime | None


class OAuthProvider(Protocol):
    provider_id: str

    def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str: ...

    def exchange(self, *, code: str, code_verifier: str) -> OAuthTokens: ...

    def revoke(self, credentials: dict[str, Any]) -> None: ...


def _https_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ControlError(f"{label}_invalid", 500)
    return value


class GoogleOAuthProvider:
    provider_id = "google"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        authorization_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint: str = "https://oauth2.googleapis.com/token",
        userinfo_endpoint: str = "https://openidconnect.googleapis.com/v1/userinfo",
        revoke_endpoint: str = "https://oauth2.googleapis.com/revoke",
    ):
        if (
            not client_id
            or not client_secret
            or len(client_id) > 512
            or len(client_secret) > 4096
        ):
            raise ControlError("google_oauth_configuration_invalid", 500)
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = _https_url(
            redirect_uri, label="google_redirect_uri"
        )
        self.authorization_endpoint = _https_url(
            authorization_endpoint, label="google_authorization_endpoint"
        )
        self.token_endpoint = _https_url(
            token_endpoint, label="google_token_endpoint"
        )
        self.userinfo_endpoint = _https_url(
            userinfo_endpoint, label="google_userinfo_endpoint"
        )
        self.revoke_endpoint = _https_url(
            revoke_endpoint, label="google_revoke_endpoint"
        )

    @classmethod
    def from_env(cls) -> "GoogleOAuthProvider":
        return cls(
            client_id=os.environ.get("RECALL_GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("RECALL_GOOGLE_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("RECALL_GOOGLE_REDIRECT_URI", ""),
        )

    @staticmethod
    def _opener() -> urllib.request.OpenerDirector:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        return urllib.request.build_opener(NoRedirect())

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ControlError("google_oauth_upstream_invalid", 502)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ControlError("google_oauth_upstream_invalid", 502) from None
        if not isinstance(value, dict):
            raise ControlError("google_oauth_upstream_invalid", 502)
        return value

    @staticmethod
    def _post(url: str, fields: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=urlencode(fields).encode(),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with GoogleOAuthProvider._opener().open(
                request, timeout=20
            ) as response:
                value = GoogleOAuthProvider._json(response)
        except (
            OSError,
            ValueError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ):
            raise ControlError("google_oauth_upstream_failed", 502) from None
        return value

    def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return self.authorization_endpoint + "?" + query

    def exchange(self, *, code: str, code_verifier: str) -> OAuthTokens:
        if not code or len(code) > 4096:
            raise ControlError("oauth_code_invalid")
        token = self._post(
            self.token_endpoint,
            {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_in = token.get("expires_in")
        scope = token.get("scope", "")
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > 8192
            or not isinstance(refresh_token, str)
            or not refresh_token
            or len(refresh_token) > 8192
            or type(expires_in) is not int
            or not 1 <= expires_in <= 604800
            or not isinstance(scope, str)
        ):
            raise ControlError("google_oauth_token_invalid", 502)
        request = urllib.request.Request(
            self.userinfo_endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener().open(request, timeout=20) as response:
                userinfo = self._json(response)
        except (
            OSError,
            ValueError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ):
            raise ControlError("google_oauth_upstream_failed", 502) from None
        subject_id = userinfo.get("sub") if isinstance(userinfo, dict) else None
        if not isinstance(subject_id, str) or not 1 <= len(subject_id) <= 256:
            raise ControlError("google_oauth_subject_invalid", 502)
        granted = tuple(sorted(set(scope.split())))
        if (
            not granted
            or len(granted) > 64
            or any(not 1 <= len(value) <= 512 for value in granted)
        ):
            raise ControlError("google_oauth_scope_invalid", 502)
        credentials = {
            "type": "authorized_user",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "token_uri": self.token_endpoint,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token.get("token_type", "Bearer"),
        }
        return OAuthTokens(
            subject_id=subject_id,
            granted_scopes=granted,
            credentials=credentials,
            expires_at=_now() + timedelta(seconds=expires_in),
        )

    def revoke(self, credentials: dict[str, Any]) -> None:
        token = credentials.get("refresh_token") or credentials.get("access_token")
        if not isinstance(token, str) or not token:
            return
        request = urllib.request.Request(
            self.revoke_endpoint,
            data=urlencode({"token": token}).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self._opener().open(request, timeout=20):
                return
        except (
            OSError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ):
            raise ControlError("google_oauth_revoke_failed", 502) from None


class ControlPlane:
    """One control contract for browser, remote worker, and macOS clients."""

    def __init__(
        self,
        store: Any,
        secret_box: SecretBox,
        providers: dict[str, OAuthProvider],
    ):
        self.store = store
        self.secret_box = secret_box
        self.providers = dict(providers)

    @classmethod
    def from_env(cls, store: Any) -> "ControlPlane":
        google_values = (
            os.environ.get("RECALL_GOOGLE_CLIENT_ID", ""),
            os.environ.get("RECALL_GOOGLE_CLIENT_SECRET", ""),
            os.environ.get("RECALL_GOOGLE_REDIRECT_URI", ""),
        )
        if any(google_values) and not all(google_values):
            raise ControlError("google_oauth_configuration_invalid", 500)
        providers: dict[str, OAuthProvider] = {}
        if all(google_values):
            providers["google"] = GoogleOAuthProvider.from_env()
        return cls(store, SecretBox.from_env(), providers)

    def create_admin_token(
        self,
        name: str,
        *,
        principal_id: str,
        expires_in_days: int = 30,
    ) -> dict[str, Any]:
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 128
            or not isinstance(principal_id, str)
            or not AUTHORITY_RE.fullmatch(principal_id)
            or type(expires_in_days) is not int
            or not 1 <= expires_in_days <= 365
        ):
            raise ControlError("admin_credential_invalid")
        token = "rca_" + secrets.token_urlsafe(32)
        credential_id = uuid.uuid4()
        expires_at = _now() + timedelta(days=expires_in_days)
        with self.store.connect() as connection:
            access = connection.execute(
                """SELECT 1 FROM brain_access_grants
                   WHERE principal_id=%s
                     AND permission IN ('owner','admin')
                   LIMIT 1""",
                (principal_id,),
            ).fetchone()
            if not access:
                raise ControlError("admin_access_missing", 403)
            connection.execute(
                """INSERT INTO admin_credentials(
                       id,name,token_sha256,principal_id,audience,scopes,expires_at
                   ) VALUES (%s,%s,%s,%s,'recall-admin',ARRAY['manage'],%s)""",
                (
                    credential_id,
                    name,
                    _digest(token),
                    principal_id,
                    expires_at,
                ),
            )
        return {
            "id": str(credential_id),
            "name": name,
            "principal_id": principal_id,
            "expires_at": expires_at.isoformat(),
            "token": token,
        }

    def revoke_admin_token(self, name: str) -> bool:
        with self.store.connect() as connection:
            row = connection.execute(
                """UPDATE admin_credentials SET revoked_at=now()
                   WHERE name=%s AND revoked_at IS NULL RETURNING id""",
                (name,),
            ).fetchone()
            if row:
                connection.execute(
                    """UPDATE admin_sessions SET revoked_at=now()
                       WHERE credential_id=%s AND revoked_at IS NULL""",
                    (row["id"],),
                )
        return bool(row)

    def exchange_admin_token(self, token: str) -> dict[str, str]:
        if not isinstance(token, str) or not ADMIN_TOKEN_RE.fullmatch(token):
            raise ControlError("admin_auth_invalid", 401)
        session = "rcs_" + secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(hours=12)
        with self.store.connect() as connection:
            credential = connection.execute(
                """SELECT id,principal_id FROM admin_credentials
                   WHERE token_sha256=%s AND audience='recall-admin'
                     AND revoked_at IS NULL AND expires_at>now()""",
                (_digest(token),),
            ).fetchone()
            if not credential:
                raise ControlError("admin_auth_invalid", 401)
            connection.execute(
                """INSERT INTO admin_sessions(
                       id,credential_id,session_sha256,csrf_sha256,expires_at
                   ) VALUES (%s,%s,%s,%s,%s)""",
                (
                    uuid.uuid4(),
                    credential["id"],
                    _digest(session),
                    _digest(csrf),
                    expires_at,
                ),
            )
        return {
            "session": session,
            "csrf": csrf,
            "expires_at": expires_at.isoformat(),
        }

    def authenticate_session(
        self,
        token: str,
        *,
        csrf: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(token, str) or not SESSION_TOKEN_RE.fullmatch(token):
            raise ControlError("admin_session_invalid", 401)
        with self.store.connect() as connection:
            row = connection.execute(
                """SELECT session.id,credential.principal_id,session.csrf_sha256
                   FROM admin_sessions session
                   JOIN admin_credentials credential
                     ON credential.id=session.credential_id
                   WHERE session.session_sha256=%s
                     AND session.revoked_at IS NULL
                     AND session.expires_at>now()
                     AND credential.revoked_at IS NULL
                     AND credential.expires_at>now()""",
                (_digest(token),),
            ).fetchone()
        if not row:
            raise ControlError("admin_session_invalid", 401)
        if csrf is not None and (
            not isinstance(csrf, str)
            or not secrets.compare_digest(row["csrf_sha256"], _digest(csrf))
        ):
            raise ControlError("admin_csrf_invalid", 403)
        return {
            "session_id": str(row["id"]),
            "principal_id": row["principal_id"],
        }

    def _brain_rows(self, principal_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            return connection.execute(
                """SELECT space.tenant_id,space.slug,space.brain_kind,
                          organization.display_name,access.permission
                   FROM brain_access_grants access
                   JOIN brain_spaces space USING(tenant_id)
                   JOIN brain_organizations organization USING(organization_id)
                   WHERE access.principal_id=%s
                   ORDER BY space.brain_kind,space.slug""",
                (principal_id,),
            ).fetchall()

    def state(self, principal_id: str) -> dict[str, Any]:
        brains = self._brain_rows(principal_id)
        with self.store.connect() as connection:
            installations = connection.execute(
                """SELECT installation.id,installation.tenant_id,
                          installation.connector_id,installation.source_id,
                          installation.execution,installation.device_id,
                          installation.state,
                          installation.privacy_mode,installation.selectors,
                          installation.revision,installation.last_error_code,
                          installation.last_success_at,
                          installation.last_started_at,
                          installation.run_after,
                          installation.failure_count,
                          connection.provider,connection.status AS connection_status
                   FROM connector_installations installation
                   LEFT JOIN provider_connections connection
                     ON connection.id=installation.connection_id
                   WHERE installation.principal_id=%s
                     AND installation.state<>'uninstalled'
                   ORDER BY installation.connector_id,installation.tenant_id""",
                (principal_id,),
            ).fetchall()
            connections = connection.execute(
                """SELECT id,provider,status,granted_scopes,expires_at,updated_at
                   FROM provider_connections
                   WHERE principal_id=%s AND status<>'revoked'
                   ORDER BY provider,created_at""",
                (principal_id,),
            ).fetchall()
        catalog = []
        for item in REGISTRY:
            if not isinstance(item, ConnectorDefinitionV3):
                continue
            public = item.to_public()
            catalog.append(
                {
                    "connector_id": item.connector_id,
                    "source_family": item.source_family,
                    "placement": public["placement"],
                    "auth": public["auth"],
                    "privacy_modes": public["policy"]["privacy_modes"],
                    "default_privacy_mode": public["policy"][
                        "default_privacy_mode"
                    ],
                    "selection_fields": public["selection_fields"],
                }
            )
        return {
            "schema_version": 1,
            "mode": "connector-control-state",
            "principal": {"id": principal_id},
            "brains": brains,
            "providers": [
                {"id": provider_id, "status": "available"}
                for provider_id in sorted(self.providers)
            ],
            "catalog": sorted(catalog, key=lambda value: value["connector_id"]),
            "connections": connections,
            "installations": installations,
        }

    def create_device_installation(
        self,
        *,
        principal_id: str,
        connector_id: str,
        tenant_id: str,
        device_id: str,
        source_id: str,
        privacy_mode: str,
        selectors: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            not isinstance(connector_id, str)
            or not isinstance(tenant_id, str)
            or not AUTHORITY_RE.fullmatch(tenant_id)
            or not isinstance(device_id, str)
            or not DEVICE_RE.fullmatch(device_id)
            or not isinstance(source_id, str)
            or not AUTHORITY_RE.fullmatch(source_id)
        ):
            raise ControlError("device_route_invalid")
        try:
            item = definition(connector_id)
        except ConnectorRegistryError:
            raise ControlError("device_connector_invalid") from None
        if (
            not isinstance(item, ConnectorDefinitionV3)
            or item.placement.execution not in {"source_local", "either"}
            or privacy_mode not in item.privacy_modes
            or not isinstance(selectors, dict)
            or set(selectors) - set(item.selection_fields)
        ):
            raise ControlError("device_connector_invalid")
        normalized_selectors = {
            key: _selector(value) for key, value in sorted(selectors.items())
        }
        encoded = json.dumps(
            normalized_selectors,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode()) > 16 * 1024:
            raise ControlError("device_selectors_invalid")
        installation_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        credential_name = f"device-{credential_id}"
        token = "rcl_" + secrets.token_urlsafe(32)
        with self.store.connect() as connection:
            with connection.transaction():
                brain = connection.execute(
                    """SELECT 1 FROM brain_access_grants
                       WHERE tenant_id=%s AND principal_id=%s
                         AND permission IN ('owner','admin')
                       FOR UPDATE""",
                    (tenant_id, principal_id),
                ).fetchone()
                if not brain:
                    raise ControlError("device_brain_forbidden", 403)
                replaced = connection.execute(
                    """UPDATE connector_installations
                       SET state='revoked',revision=revision+1,updated_at=now()
                       WHERE principal_id=%s AND connector_id=%s AND device_id=%s
                         AND state NOT IN ('revoked','uninstalled')
                       RETURNING id""",
                    (principal_id, connector_id, device_id),
                ).fetchall()
                replaced_ids = [row["id"] for row in replaced]
                if replaced_ids:
                    connection.execute(
                        """UPDATE collector_credentials SET revoked_at=now()
                           WHERE installation_id=ANY(%s) AND revoked_at IS NULL""",
                        (replaced_ids,),
                    )
                connection.execute(
                    """INSERT INTO connector_installations(
                           id,tenant_id,principal_id,connector_id,source_id,
                           connection_id,execution,device_id,state,privacy_mode,
                           selectors
                       ) VALUES (%s,%s,%s,%s,%s,NULL,'source_local',%s,
                                 'enabled',%s,%s::jsonb)""",
                    (
                        installation_id,
                        tenant_id,
                        principal_id,
                        connector_id,
                        source_id,
                        device_id,
                        privacy_mode,
                        encoded,
                    ),
                )
                connection.execute(
                    """INSERT INTO collector_credentials(
                           id,name,token_sha256,tenant_id,source_id,scopes,
                           principal_id,installation_id
                       ) VALUES (%s,%s,%s,%s,%s,ARRAY['write'],%s,%s)""",
                    (
                        credential_id,
                        credential_name,
                        _digest(token),
                        tenant_id,
                        source_id,
                        principal_id,
                        installation_id,
                    ),
                )
                connection.execute(
                    """INSERT INTO control_audit_events(
                           id,principal_id,operation,status,target_sha256
                       ) VALUES (%s,%s,'device.route','success',%s)""",
                    (
                        uuid.uuid4(),
                        principal_id,
                        hashlib.sha256(str(installation_id).encode()).hexdigest(),
                    ),
                )
        return {
            "schema_version": 1,
            "mode": "device-route",
            "installation_id": str(installation_id),
            "connector_id": connector_id,
            "tenant_id": tenant_id,
            "device_id": device_id,
            "source_id": source_id,
            "privacy_mode": privacy_mode,
            "state": "enabled",
            "token": token,
            "replaced": len(replaced_ids),
        }

    def start_oauth(
        self,
        *,
        principal_id: str,
        provider_id: str,
        routes: list[dict[str, Any]],
    ) -> dict[str, str]:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ControlError("oauth_provider_unsupported")
        if (
            not isinstance(routes, list)
            or not 1 <= len(routes) <= 8
            or any(not isinstance(route, dict) for route in routes)
        ):
            raise ControlError("oauth_routes_invalid")
        brains = {
            row["tenant_id"]: row
            for row in self._brain_rows(principal_id)
            if row["permission"] in {"owner", "admin"}
        }
        normalized: list[dict[str, Any]] = []
        scopes: set[str] = set()
        if provider_id == "google":
            scopes.add("openid")
        identities: set[tuple[str, str]] = set()
        for route in routes:
            if set(route) != {
                "connector_id",
                "tenant_id",
                "privacy_mode",
                "selectors",
            }:
                raise ControlError("oauth_route_invalid")
            connector_id = route["connector_id"]
            tenant_id = route["tenant_id"]
            if provider_id == "google" and connector_id not in GOOGLE_CONNECTORS:
                raise ControlError("oauth_connector_invalid")
            if tenant_id not in brains:
                raise ControlError("oauth_brain_forbidden", 403)
            try:
                item = definition(connector_id)
            except ConnectorRegistryError:
                raise ControlError("oauth_connector_invalid") from None
            if (
                not isinstance(item, ConnectorDefinitionV3)
                or item.auth.kind != "oauth2"
                or item.placement.execution not in {"remote_worker", "either"}
            ):
                raise ControlError("oauth_connector_invalid")
            identity = (tenant_id, connector_id)
            if identity in identities:
                raise ControlError("oauth_route_duplicate")
            identities.add(identity)
            privacy_mode = route["privacy_mode"]
            if privacy_mode not in item.privacy_modes:
                raise ControlError("oauth_privacy_invalid")
            selectors = route["selectors"]
            if (
                not isinstance(selectors, dict)
                or set(selectors) - set(item.selection_fields)
            ):
                raise ControlError("oauth_selectors_invalid")
            selectors = {
                key: _selector(value)
                for key, value in sorted(selectors.items())
            }
            encoded = json.dumps(
                selectors,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(encoded.encode()) > 16 * 1024:
                raise ControlError("oauth_selectors_invalid")
            scopes.update(item.auth.minimum_scopes)
            normalized.append(
                {
                    "connector_id": connector_id,
                    "tenant_id": tenant_id,
                    "privacy_mode": privacy_mode,
                    "selectors": selectors,
                }
            )
        code_verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(48)
        context = self.secret_box.seal(
            {
                "provider": provider_id,
                "principal_id": principal_id,
                "routes": normalized,
                "code_verifier": code_verifier,
            },
            purpose="oauth-session",
        )
        with self.store.connect() as connection:
            connection.execute(
                """INSERT INTO oauth_sessions(
                       state_sha256,principal_id,provider,encrypted_context,
                       encryption_key_id,expires_at
                   ) VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    _digest(state),
                    principal_id,
                    provider_id,
                    context,
                    self.secret_box.key_id,
                    _now() + timedelta(minutes=10),
                ),
            )
        return {
            "provider": provider_id,
            "authorization_url": provider.authorization_url(
                state=state,
                code_challenge=challenge,
                scopes=tuple(sorted(scopes)),
            ),
        }

    def complete_oauth(
        self,
        *,
        provider_id: str,
        state: str,
        code: str,
    ) -> dict[str, Any]:
        if not isinstance(state, str) or not STATE_RE.fullmatch(state):
            raise ControlError("oauth_state_invalid", 403)
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ControlError("oauth_provider_unsupported")
        with self.store.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    """SELECT principal_id,provider,encrypted_context,
                              encryption_key_id
                       FROM oauth_sessions
                       WHERE state_sha256=%s AND consumed_at IS NULL
                         AND expires_at>now()
                       FOR UPDATE""",
                    (_digest(state),),
                ).fetchone()
                if (
                    not row
                    or row["provider"] != provider_id
                    or row["encryption_key_id"] != self.secret_box.key_id
                ):
                    raise ControlError("oauth_state_invalid", 403)
                connection.execute(
                    """UPDATE oauth_sessions SET consumed_at=now()
                       WHERE state_sha256=%s""",
                    (_digest(state),),
                )
        context = self.secret_box.open(
            bytes(row["encrypted_context"]), purpose="oauth-session"
        )
        if (
            context.get("principal_id") != row["principal_id"]
            or context.get("provider") != provider_id
        ):
            raise ControlError("oauth_state_invalid", 403)
        administrable = {
            value["tenant_id"]
            for value in self._brain_rows(row["principal_id"])
            if value["permission"] in {"owner", "admin"}
        }
        if any(
            route.get("tenant_id") not in administrable
            for route in context.get("routes", [])
        ):
            raise ControlError("oauth_brain_forbidden", 403)
        tokens = provider.exchange(
            code=code, code_verifier=context.get("code_verifier", "")
        )
        required_scopes: set[str] = set()
        for route in context.get("routes", []):
            item = definition(route["connector_id"])
            required_scopes.update(item.auth.minimum_scopes)
        if not required_scopes.issubset(set(tokens.granted_scopes)):
            raise ControlError("oauth_scope_insufficient", 403)
        connection_id = uuid.uuid4()
        encrypted = self.secret_box.seal(
            tokens.credentials, purpose=f"provider-connection:{connection_id}"
        )
        installed = []
        with self.store.connect() as connection:
            with connection.transaction():
                prior = connection.execute(
                    """SELECT id FROM provider_connections
                       WHERE principal_id=%s AND provider=%s AND subject_id=%s""",
                    (row["principal_id"], provider_id, tokens.subject_id),
                ).fetchone()
                if prior:
                    connection_id = prior["id"]
                    encrypted = self.secret_box.seal(
                        tokens.credentials,
                        purpose=f"provider-connection:{connection_id}",
                    )
                    connection.execute(
                        """UPDATE provider_connections SET
                             status='connected',granted_scopes=%s,
                             encrypted_credentials=%s,encryption_key_id=%s,
                             expires_at=%s,updated_at=now(),revoked_at=NULL
                           WHERE id=%s""",
                        (
                            list(tokens.granted_scopes),
                            encrypted,
                            self.secret_box.key_id,
                            tokens.expires_at,
                            connection_id,
                        ),
                    )
                else:
                    connection.execute(
                        """INSERT INTO provider_connections(
                               id,principal_id,provider,subject_id,status,
                               granted_scopes,encrypted_credentials,
                               encryption_key_id,expires_at
                           ) VALUES (%s,%s,%s,%s,'connected',%s,%s,%s,%s)""",
                        (
                            connection_id,
                            row["principal_id"],
                            provider_id,
                            tokens.subject_id,
                            list(tokens.granted_scopes),
                            encrypted,
                            self.secret_box.key_id,
                            tokens.expires_at,
                        ),
                    )
                for route in context["routes"]:
                    installation_id = uuid.uuid4()
                    source_id = (
                        f"source:{route['connector_id']}:{uuid.uuid4().hex}"
                    )
                    connection.execute(
                        """INSERT INTO connector_installations(
                               id,tenant_id,principal_id,connector_id,source_id,
                               connection_id,execution,state,privacy_mode,selectors
                           ) VALUES (%s,%s,%s,%s,%s,%s,'remote_worker','enabled',%s,%s)
                           ON CONFLICT(tenant_id,principal_id,connector_id)
                           WHERE device_id IS NULL
                           DO UPDATE SET
                             connection_id=excluded.connection_id,
                             state='enabled',
                             privacy_mode=excluded.privacy_mode,
                             selectors=excluded.selectors,
                             revision=connector_installations.revision+1,
                             last_error_code=NULL,
                             updated_at=now()""",
                        (
                            installation_id,
                            route["tenant_id"],
                            row["principal_id"],
                            route["connector_id"],
                            source_id,
                            connection_id,
                            route["privacy_mode"],
                            json.dumps(route["selectors"]),
                        ),
                    )
                    installed.append(
                        {
                            "tenant_id": route["tenant_id"],
                            "connector_id": route["connector_id"],
                            "state": "enabled",
                        }
                    )
                connection.execute(
                    """INSERT INTO control_audit_events(
                           id,principal_id,operation,status,target_sha256
                       ) VALUES (%s,%s,'oauth.complete','success',%s)""",
                    (
                        uuid.uuid4(),
                        row["principal_id"],
                        hashlib.sha256(str(connection_id).encode()).hexdigest(),
                    ),
                )
        return {
            "provider": provider_id,
            "connection_id": str(connection_id),
            "installations": installed,
        }

    def transition_installation(
        self,
        *,
        principal_id: str,
        installation_id: str,
        action: str,
    ) -> dict[str, Any]:
        transition = TRANSITIONS.get(action)
        try:
            target_id = uuid.UUID(installation_id)
        except (ValueError, TypeError):
            raise ControlError("installation_invalid") from None
        if transition is None:
            raise ControlError("installation_action_invalid")
        allowed, target = transition
        with self.store.connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    """SELECT state,revision FROM connector_installations
                       WHERE id=%s AND principal_id=%s FOR UPDATE""",
                    (target_id, principal_id),
                ).fetchone()
                if not row:
                    raise ControlError("installation_not_found", 404)
                if row["state"] == target:
                    return {
                        "installation_id": installation_id,
                        "state": target,
                        "revision": row["revision"],
                        "replay": True,
                    }
                if row["state"] not in allowed:
                    raise ControlError("installation_transition_invalid", 409)
                revision = row["revision"] + 1
                connection.execute(
                    """UPDATE connector_installations
                       SET state=%s,revision=%s,updated_at=now()
                       WHERE id=%s""",
                    (target, revision, target_id),
                )
                if target in {"revoked", "uninstalled"}:
                    connection.execute(
                        """UPDATE collector_credentials SET revoked_at=now()
                           WHERE installation_id=%s AND revoked_at IS NULL""",
                        (target_id,),
                    )
        return {
            "installation_id": installation_id,
            "state": target,
            "revision": revision,
            "replay": False,
        }

    def revoke_connection(
        self,
        *,
        principal_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        try:
            target_id = uuid.UUID(connection_id)
        except (TypeError, ValueError):
            raise ControlError("connection_invalid") from None
        with self.store.connect() as connection:
            row = connection.execute(
                """SELECT provider,encrypted_credentials,encryption_key_id,status
                   FROM provider_connections
                   WHERE id=%s AND principal_id=%s""",
                (target_id, principal_id),
            ).fetchone()
        if not row:
            raise ControlError("connection_not_found", 404)
        if row["status"] == "revoked":
            return {"connection_id": connection_id, "status": "revoked", "replay": True}
        if row["encryption_key_id"] != self.secret_box.key_id:
            raise ControlError("control_secret_key_unavailable", 500)
        credentials = self.secret_box.open(
            bytes(row["encrypted_credentials"]),
            purpose=f"provider-connection:{target_id}",
        )
        provider = self.providers.get(row["provider"])
        if provider is None:
            raise ControlError("oauth_provider_unsupported", 500)
        provider.revoke(credentials)
        empty = self.secret_box.seal(
            {}, purpose=f"provider-connection:{target_id}"
        )
        with self.store.connect() as connection:
            with connection.transaction():
                connection.execute(
                    """UPDATE provider_connections SET
                         status='revoked',encrypted_credentials=%s,
                         updated_at=now(),revoked_at=now()
                       WHERE id=%s AND principal_id=%s""",
                    (empty, target_id, principal_id),
                )
                changed = connection.execute(
                    """UPDATE connector_installations
                       SET state='revoked',revision=revision+1,updated_at=now()
                       WHERE connection_id=%s AND principal_id=%s
                         AND state NOT IN ('revoked','uninstalled')
                       RETURNING id""",
                    (target_id, principal_id),
                ).fetchall()
        return {
            "connection_id": connection_id,
            "status": "revoked",
            "installations_revoked": len(changed),
            "replay": False,
        }


__all__ = [
    "ControlError",
    "ControlPlane",
    "GoogleOAuthProvider",
    "OAuthTokens",
    "SecretBox",
]
