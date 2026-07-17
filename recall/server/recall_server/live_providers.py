from __future__ import annotations

import hashlib
import json
import os
import ssl
from typing import Any, Protocol
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit

from .capabilities import CapabilityError, validate_connection_policy
from .deployment import IMAGE_RE, MODEL_RE


MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024


class LiveProviderError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class JsonProvider(Protocol):
    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]: ...


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


class HttpsJsonProvider:
    """Small fail-closed JSON transport for provider control planes."""

    def __init__(
        self,
        base_url: str,
        authorization: str,
        *,
        hostname: str,
        timeout_seconds: float = 20,
        opener: Any | None = None,
    ):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.rstrip("/")
            or "\r" in authorization
            or "\n" in authorization
            or not authorization
            or len(authorization) > 4096
            or not 0 < timeout_seconds <= 60
        ):
            raise LiveProviderError("provider_transport_configuration_invalid")
        self.base_url = base_url.rstrip("/")
        self.authorization = authorization
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.build_opener(
            _RejectRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        parsed = urlsplit(path)
        if (
            method not in {"GET", "POST"}
            or not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or (method == "GET" and body is not None)
            or (method == "POST" and body is None)
        ):
            raise LiveProviderError("provider_request_invalid")
        payload = (
            None
            if body is None
            else json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        )
        headers = {
            "Accept": "application/json",
            "Authorization": self.authorization,
            "User-Agent": "recall-managed-core/1",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            response = self.opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            raise LiveProviderError("provider_transport_failed") from error
        try:
            status = int(response.status)
            payload = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except (AttributeError, OSError, ValueError) as error:
            raise LiveProviderError("provider_transport_failed") from error
        finally:
            response.close()
        if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
            raise LiveProviderError("provider_response_too_large")
        if not payload:
            return status, None
        try:
            return status, json.loads(payload, object_pairs_hook=_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise LiveProviderError("provider_response_invalid") from error


def planetscale_provider(
    service_token_id: str,
    service_token: str,
    *,
    opener: Any | None = None,
) -> HttpsJsonProvider:
    return HttpsJsonProvider(
        "https://api.planetscale.com/v1",
        f"{service_token_id}:{service_token}",
        hostname="api.planetscale.com",
        opener=opener,
    )


def render_provider(api_key: str, *, opener: Any | None = None) -> HttpsJsonProvider:
    return HttpsJsonProvider(
        "https://api.render.com/v1",
        f"Bearer {api_key}",
        hostname="api.render.com",
        opener=opener,
    )


def _receipt(provider: str, logical_id: str, resource_ids: list[str]) -> str:
    value = json.dumps(
        {
            "provider": provider,
            "logical_id": logical_id,
            "resource_ids": resource_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _selection(desired: dict[str, Any], *, region: str, billing: str) -> None:
    if desired.get("region") != region or desired.get("billing_plan") != billing:
        raise LiveProviderError("approval_selection_mismatch")


class PlanetScaleDatabaseAdapter:
    def __init__(
        self,
        provider: JsonProvider,
        *,
        organization: str,
        database_name: str,
        region_selection: str,
        billing_selection: str,
        region: str,
        cluster_size: str,
        replicas: int,
        major_version: str,
        minimum_storage_bytes: int,
        maximum_storage_bytes: int,
    ):
        if (
            not organization
            or not database_name
            or not region
            or not cluster_size.startswith("PS_")
            or replicas not in {0, 2}
            or not major_version.isdigit()
            or minimum_storage_bytes < 10 * 1024**3
            or maximum_storage_bytes < minimum_storage_bytes
            or maximum_storage_bytes > 64 * 1024**4
        ):
            raise LiveProviderError("database_configuration_invalid")
        self.provider = provider
        self.organization = organization
        self.database_name = database_name
        self.region_selection = region_selection
        self.billing_selection = billing_selection
        self.region = region
        self.cluster_size = cluster_size
        self.replicas = replicas
        self.major_version = major_version
        self.minimum_storage_bytes = minimum_storage_bytes
        self.maximum_storage_bytes = maximum_storage_bytes

    def _path(self) -> str:
        return (
            f"/organizations/{quote(self.organization, safe='')}/databases/"
            f"{quote(self.database_name, safe='')}"
        )

    def _validate_existing(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LiveProviderError("provider_response_invalid")
        region = value.get("region")
        if (
            value.get("name") != self.database_name
            or value.get("kind") != "postgresql"
            or not isinstance(region, dict)
            or region.get("slug") != self.region
            or value.get("state") not in {"pending", "ready"}
            or not isinstance(value.get("id"), str)
        ):
            raise LiveProviderError("database_drift")
        return value

    def ensure(self, logical_id: str, desired: dict[str, Any]) -> dict[str, str]:
        _selection(
            desired,
            region=self.region_selection,
            billing=self.billing_selection,
        )
        if (
            desired.get("adapter") != "postgres"
            or desired.get("provider") != "planetscale"
            or desired.get("tls_mode") != "verify-full"
        ):
            raise LiveProviderError("database_contract_invalid")
        status, current = self.provider.request("GET", self._path())
        if status == 404:
            status, current = self.provider.request(
                "POST",
                (f"/organizations/{quote(self.organization, safe='')}/databases"),
                {
                    "name": self.database_name,
                    "kind": "postgresql",
                    "cluster_size": self.cluster_size,
                    "region": self.region,
                    "replicas": self.replicas,
                    "major_version": self.major_version,
                    "storage": {
                        "minimum_storage_bytes": self.minimum_storage_bytes,
                        "maximum_storage_bytes": self.maximum_storage_bytes,
                    },
                },
            )
            if status != 201:
                raise LiveProviderError("database_create_failed")
            action = "created"
        elif status == 200:
            action = "unchanged"
        else:
            raise LiveProviderError("database_lookup_failed")
        database = self._validate_existing(current)
        return {
            "action": action,
            "receipt_sha256": _receipt("planetscale", logical_id, [database["id"]]),
        }


class _RenderPrivateServices:
    def __init__(
        self,
        provider: JsonProvider,
        *,
        owner_id: str,
        region: str,
    ):
        if not owner_id or not region:
            raise LiveProviderError("render_configuration_invalid")
        self.provider = provider
        self.owner_id = owner_id
        self.region = region

    def find(self, name: str) -> dict[str, Any] | None:
        query = urlencode({"ownerId": self.owner_id, "name": name, "limit": 20})
        status, body = self.provider.request("GET", f"/services?{query}")
        if status != 200 or not isinstance(body, list):
            raise LiveProviderError("render_lookup_failed")
        matches = []
        for item in body:
            service = (
                item.get("service")
                if isinstance(item, dict) and "service" in item
                else item
            )
            if isinstance(service, dict) and service.get("name") == name:
                matches.append(service)
        if len(matches) > 1:
            raise LiveProviderError("render_duplicate_service")
        return matches[0] if matches else None

    def validate(
        self,
        service: object,
        *,
        name: str,
        image: str,
        plan: str,
        expected_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(service, dict):
            raise LiveProviderError("provider_response_invalid")
        details = service.get("serviceDetails")
        if (
            service.get("name") != name
            or service.get("ownerId") != self.owner_id
            or service.get("type") != "private_service"
            or service.get("imagePath") != image
            or not isinstance(details, dict)
            or details.get("runtime") != "image"
            or details.get("region") != self.region
            or details.get("plan") != plan
            or not isinstance(details.get("url"), str)
            or not isinstance(service.get("id"), str)
        ):
            raise LiveProviderError("render_service_drift")
        if any(
            details.get(key) != value for key, value in (expected_details or {}).items()
        ):
            raise LiveProviderError("render_service_drift")
        return service

    def _configuration(
        self, service_id: str, resource: str, wrapper: str
    ) -> dict[str, str]:
        status, body = self.provider.request(
            "GET",
            f"/services/{quote(service_id, safe='')}/{resource}?limit=100",
        )
        if status != 200 or not isinstance(body, list) or len(body) >= 100:
            raise LiveProviderError("render_configuration_lookup_failed")
        values: dict[str, str] = {}
        for item in body:
            value = item.get(wrapper) if isinstance(item, dict) else None
            key_name = "key" if wrapper == "envVar" else "name"
            content_name = "value" if wrapper == "envVar" else "content"
            key = value.get(key_name) if isinstance(value, dict) else None
            content = value.get(content_name) if isinstance(value, dict) else None
            if (
                not isinstance(key, str)
                or not isinstance(content, str)
                or key in values
            ):
                raise LiveProviderError("render_configuration_lookup_failed")
            values[key] = content
        return values

    def validate_configuration(
        self,
        service_id: str,
        *,
        env_vars: list[dict[str, str]] | None = None,
        secret_files: list[dict[str, str]] | None = None,
    ) -> None:
        expected_env = {item["key"]: item["value"] for item in (env_vars or [])}
        expected_files = {
            item["name"]: item["content"] for item in (secret_files or [])
        }
        if (
            self._configuration(service_id, "env-vars", "envVar") != expected_env
            or self._configuration(service_id, "secret-files", "secretFile")
            != expected_files
        ):
            raise LiveProviderError("render_configuration_drift")

    def create(
        self,
        *,
        name: str,
        image: str,
        plan: str,
        env_vars: list[dict[str, str]] | None = None,
        secret_files: list[dict[str, str]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not IMAGE_RE.fullmatch(image):
            raise LiveProviderError("render_image_unpinned")
        service_details = {
            "runtime": "image",
            "plan": plan,
            "region": self.region,
            "numInstances": 1,
            **(details or {}),
        }
        status, body = self.provider.request(
            "POST",
            "/services",
            {
                "type": "private_service",
                "name": name,
                "ownerId": self.owner_id,
                "autoDeploy": "no",
                "image": {
                    "ownerId": self.owner_id,
                    "imagePath": image,
                },
                "envVars": env_vars or [],
                "secretFiles": secret_files or [],
                "serviceDetails": service_details,
            },
        )
        service = body.get("service") if isinstance(body, dict) else None
        if status != 201:
            raise LiveProviderError("render_create_failed")
        validated = self.validate(
            service,
            name=name,
            image=image,
            plan=plan,
            expected_details=details,
        )
        self.validate_configuration(
            validated["id"],
            env_vars=env_vars,
            secret_files=secret_files,
        )
        return validated


class RenderPrivateStackAdapter:
    def __init__(
        self,
        provider: JsonProvider,
        context: dict[str, str],
        *,
        owner_id: str,
        region_selection: str,
        billing_selection: str,
        region: str,
        core_name: str,
        core_plan: str,
        embedding_api_key: str,
        database_url: str,
    ):
        self.services = _RenderPrivateServices(
            provider, owner_id=owner_id, region=region
        )
        self.context = context
        self.region_selection = region_selection
        self.billing_selection = billing_selection
        self.core_name = core_name
        self.core_plan = core_plan
        if (
            not embedding_api_key
            or len(embedding_api_key) > 4096
            or "\r" in embedding_api_key
            or "\n" in embedding_api_key
        ):
            raise LiveProviderError("embedding_credentials_unavailable")
        self.embedding_api_key = embedding_api_key
        try:
            validate_connection_policy(database_url, "production")
        except CapabilityError:
            raise LiveProviderError("database_url_policy_failed")
        self.database_url = database_url

    @staticmethod
    def _validate_embedding(value: object) -> dict[str, Any]:
        expected = {
            "protocol",
            "url",
            "approved_url",
            "key_ref",
            "model",
            "revision",
            "dimensions",
            "batch_size",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise LiveProviderError("render_stack_contract_invalid")
        url = value["url"]
        approved_url = value["approved_url"]
        parsed = urlsplit(url) if isinstance(url, str) else None
        approved = (
            urlsplit(approved_url) if isinstance(approved_url, str) else None
        )
        if (
            value["protocol"] not in {"voyage", "openai"}
            or parsed is None
            or approved is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or approved.scheme != "https"
            or not approved.hostname
            or approved.username
            or approved.password
            or approved.query
            or approved.fragment
            or url.rstrip("/") != approved_url.rstrip("/")
            or value["key_ref"] != "secret://runtime/RECALL_EMBEDDING_API_KEY"
            or not isinstance(value["model"], str)
            or not MODEL_RE.fullmatch(value["model"])
            or not isinstance(value["revision"], str)
            or not MODEL_RE.fullmatch(value["revision"])
            or value["dimensions"] != 512
            or type(value["dimensions"]) is not int
            or type(value["batch_size"]) is not int
            or not 1 <= value["batch_size"] <= 128
        ):
            raise LiveProviderError("render_stack_contract_invalid")
        return value

    def _core_env(self, embedding: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"key": "RECALL_DATABASE_URL", "value": self.database_url},
            {"key": "RECALL_AUTH_REQUIRED", "value": "1"},
            {"key": "RECALL_TRUST_TAILSCALE_HEADERS", "value": "0"},
            {
                "key": "RECALL_EMBEDDING_PROTOCOL",
                "value": embedding["protocol"],
            },
            {"key": "RECALL_EMBEDDING_URL", "value": embedding["url"]},
            {
                "key": "RECALL_EMBEDDING_APPROVED_URL",
                "value": embedding["approved_url"],
            },
            {
                "key": "RECALL_EMBEDDING_KEY_ENV",
                "value": "RECALL_EMBEDDING_API_KEY",
            },
            {"key": "RECALL_EMBEDDING_API_KEY", "value": self.embedding_api_key},
            {
                "key": "RECALL_EMBEDDING_MODEL",
                "value": embedding["model"],
            },
            {
                "key": "RECALL_EMBEDDING_REVISION",
                "value": embedding["revision"],
            },
            {
                "key": "RECALL_EMBEDDING_DIMENSIONS",
                "value": str(embedding["dimensions"]),
            },
            {
                "key": "RECALL_EMBEDDING_BATCH_SIZE",
                "value": str(embedding["batch_size"]),
            },
            {"key": "LOG_LEVEL", "value": "INFO"},
        ]

    def _ensure_core(
        self, image: str, embedding: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        env_vars = self._core_env(embedding)
        service = self.services.find(self.core_name)
        if service is not None:
            service = self.services.validate(
                service,
                name=self.core_name,
                image=image,
                plan=self.core_plan,
            )
            self.services.validate_configuration(service["id"], env_vars=env_vars)
            return service, False
        return (
            self.services.create(
                name=self.core_name,
                image=image,
                plan=self.core_plan,
                env_vars=env_vars,
            ),
            True,
        )

    def ensure(self, logical_id: str, desired: dict[str, Any]) -> dict[str, str]:
        _selection(
            desired,
            region=self.region_selection,
            billing=self.billing_selection,
        )
        core_image = desired.get("image")
        embedding = self._validate_embedding(desired.get("embedding"))
        if (
            desired.get("adapter") != "render-private-service"
            or desired.get("public_ingress") is not False
            or not isinstance(core_image, str)
            or not IMAGE_RE.fullmatch(core_image)
        ):
            raise LiveProviderError("render_stack_contract_invalid")
        core, core_created = self._ensure_core(core_image, embedding)
        self.context["core_url"] = core["serviceDetails"]["url"].rstrip("/")
        return {
            "action": ("created" if core_created else "unchanged"),
            "receipt_sha256": _receipt(
                "render",
                logical_id,
                [core["id"]],
            ),
        }


class RenderTailscaleGatewayAdapter:
    def __init__(
        self,
        provider: JsonProvider,
        context: dict[str, str],
        *,
        owner_id: str,
        region: str,
        name: str,
        plan: str,
        hostname: str,
        tag: str,
        client_id: str,
        client_secret: str,
    ):
        if (
            not hostname
            or not tag.startswith("tag:")
            or not client_id
            or not client_secret
        ):
            raise LiveProviderError("tailscale_configuration_invalid")
        self.services = _RenderPrivateServices(
            provider, owner_id=owner_id, region=region
        )
        self.context = context
        self.name = name
        self.plan = plan
        self.hostname = hostname
        self.tag = tag
        self.client_id = client_id
        self.client_secret = client_secret

    def ensure(self, logical_id: str, desired: dict[str, Any]) -> dict[str, str]:
        image = desired.get("gateway_image")
        port = desired.get("listen_port")
        if (
            desired.get("adapter") != "tailscale-gateway"
            or desired.get("provider_authorized") is not True
            or desired.get("route_approved") is not True
            or port != 9443
            or not isinstance(image, str)
            or not IMAGE_RE.fullmatch(image)
        ):
            raise LiveProviderError("tailscale_gateway_contract_invalid")
        core_url = self.context.get("core_url")
        parsed = urlsplit(core_url or "")
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise LiveProviderError("private_core_route_unavailable")
        serve = {
            "TCP": {str(port): {"HTTPS": True}},
            "Web": {
                f"${{TS_CERT_DOMAIN}}:{port}": {"Handlers": {"/": {"Proxy": core_url}}}
            },
        }
        env_vars = [
            {
                "key": "TS_CLIENT_ID",
                "value": "file:/etc/secrets/tailscale-client-id",
            },
            {
                "key": "TS_CLIENT_SECRET",
                "value": "file:/etc/secrets/tailscale-client-secret",
            },
            {"key": "TS_HOSTNAME", "value": self.hostname},
            {
                "key": "TS_EXTRA_ARGS",
                "value": f"--advertise-tags={self.tag}",
            },
            {"key": "TS_USERSPACE", "value": "true"},
            {"key": "TS_AUTH_ONCE", "value": "true"},
            {"key": "TS_KUBE_SECRET", "value": ""},
            {"key": "TS_STATE_DIR", "value": "/var/lib/tailscale"},
            {
                "key": "TS_SERVE_CONFIG",
                "value": "/etc/secrets/serve.json",
            },
            {"key": "TS_ENABLE_HEALTH_CHECK", "value": "true"},
            {"key": "TS_LOCAL_ADDR_PORT", "value": "[::]:10000"},
        ]
        secret_files = [
            {"name": "tailscale-client-id", "content": self.client_id},
            {
                "name": "tailscale-client-secret",
                "content": self.client_secret,
            },
            {
                "name": "serve.json",
                "content": json.dumps(serve, sort_keys=True, separators=(",", ":")),
            },
        ]
        details = {
            "disk": {
                "name": "tailscale-state",
                "mountPath": "/var/lib/tailscale",
                "sizeGB": 1,
            }
        }
        current = self.services.find(self.name)
        if current is not None:
            service = self.services.validate(
                current,
                name=self.name,
                image=image,
                plan=self.plan,
                expected_details=details,
            )
            self.services.validate_configuration(
                service["id"],
                env_vars=env_vars,
                secret_files=secret_files,
            )
            action = "unchanged"
        else:
            service = self.services.create(
                name=self.name,
                image=image,
                plan=self.plan,
                env_vars=env_vars,
                secret_files=secret_files,
                details=details,
            )
            action = "created"
        return {
            "action": action,
            "receipt_sha256": _receipt("render-tailscale", logical_id, [service["id"]]),
        }


def _required_environment(names: tuple[str, ...]) -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in names}
    if any(
        not value or "\x00" in value or "\r" in value or "\n" in value
        for value in values.values()
    ):
        raise LiveProviderError("provider_credentials_unavailable")
    return values


def build_live_adapters(
    *,
    planetscale_organization: str,
    database_name: str,
    render_owner_id: str,
    core_name: str,
    gateway_name: str,
    tailnet_hostname: str,
    tailnet_tag: str,
) -> dict[str, Any]:
    """Build the single approved Render + PlanetScale deployment profile."""

    secrets = _required_environment(
        (
            "PLANETSCALE_SERVICE_TOKEN_ID",
            "PLANETSCALE_SERVICE_TOKEN",
            "RENDER_API_KEY",
            "RECALL_DATABASE_URL",
            "RECALL_EMBEDDING_API_KEY",
            "TAILSCALE_OAUTH_CLIENT_ID",
            "TAILSCALE_OAUTH_CLIENT_SECRET",
        )
    )
    planetscale = planetscale_provider(
        secrets["PLANETSCALE_SERVICE_TOKEN_ID"],
        secrets["PLANETSCALE_SERVICE_TOKEN"],
    )
    render = render_provider(secrets["RENDER_API_KEY"])
    context: dict[str, str] = {}
    return {
        "database": PlanetScaleDatabaseAdapter(
            planetscale,
            organization=planetscale_organization,
            database_name=database_name,
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="us-east",
            cluster_size="PS_80",
            replicas=2,
            major_version="17",
            minimum_storage_bytes=50 * 1024**3,
            maximum_storage_bytes=1024 * 1024**3,
        ),
        "service": RenderPrivateStackAdapter(
            render,
            context,
            owner_id=render_owner_id,
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="virginia",
            core_name=core_name,
            core_plan="starter",
            embedding_api_key=secrets["RECALL_EMBEDDING_API_KEY"],
            database_url=secrets["RECALL_DATABASE_URL"],
        ),
        "network": RenderTailscaleGatewayAdapter(
            render,
            context,
            owner_id=render_owner_id,
            region="virginia",
            name=gateway_name,
            plan="starter",
            hostname=tailnet_hostname,
            tag=tailnet_tag,
            client_id=secrets["TAILSCALE_OAUTH_CLIENT_ID"],
            client_secret=secrets["TAILSCALE_OAUTH_CLIENT_SECRET"],
        ),
    }
