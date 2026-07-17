from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

RECALL = Path(__file__).resolve().parents[2]
SERVER = RECALL / "server"
sys.path.insert(0, str(SERVER))

from recall_server.live_providers import (  # noqa: E402
    LiveProviderError,
    PlanetScaleDatabaseAdapter,
    RenderPrivateStackAdapter,
    RenderTailscaleGatewayAdapter,
)


CORE_IMAGE = "ghcr.io/synthetic/recall@sha256:" + "a" * 64
EMBEDDING_IMAGE = "ghcr.io/synthetic/embedding@sha256:" + "b" * 64
GATEWAY_IMAGE = "docker.io/synthetic/tailscale@sha256:" + "c" * 64


class FakePlanetScale:
    def __init__(self) -> None:
        self.database: dict | None = None
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, body: dict | None = None):
        self.calls.append((method, path, body))
        if method == "GET":
            return (404, None) if self.database is None else (200, self.database)
        if method == "POST":
            self.database = {
                "id": "db-synthetic-1",
                "name": body["name"],
                "kind": body["kind"],
                "state": "ready",
                "region": {"slug": body["region"]},
            }
            return 201, self.database
        raise AssertionError((method, path))


class FakeRender:
    def __init__(self) -> None:
        self.services: dict[str, dict] = {}
        self.configuration: dict[str, dict[str, list[dict[str, str]]]] = {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, body: dict | None = None):
        self.calls.append((method, path, body))
        if method == "GET":
            if "/env-vars?" in path or "/secret-files?" in path:
                service_id = path.split("/services/", 1)[1].split("/", 1)[0]
                resource = "envVars" if "/env-vars?" in path else "secretFiles"
                wrapper = "envVar" if resource == "envVars" else "secretFile"
                return 200, [
                    {wrapper: dict(value), "cursor": str(index)}
                    for index, value in enumerate(
                        self.configuration[service_id][resource]
                    )
                ]
            name = path.split("name=", 1)[1].split("&", 1)[0]
            service = self.services.get(name)
            return 200, ([] if service is None else [{"service": service}])
        if method == "POST" and path == "/services":
            name = body["name"]
            details = body["serviceDetails"]
            service = {
                "id": f"srv-{len(self.services) + 1}",
                "name": name,
                "ownerId": body["ownerId"],
                "type": body["type"],
                "imagePath": body["image"]["imagePath"],
                "serviceDetails": {
                    "runtime": details["runtime"],
                    "region": details["region"],
                    "plan": details["plan"],
                    "url": f"http://{name}.internal:10000",
                    **{
                        key: value
                        for key, value in details.items()
                        if key not in {"runtime", "region", "plan"}
                    },
                },
            }
            self.services[name] = service
            self.configuration[service["id"]] = {
                "envVars": [
                    {"key": item["key"], "value": item["value"]}
                    for item in body["envVars"]
                ],
                "secretFiles": [
                    {"name": item["name"], "content": item["content"]}
                    for item in body["secretFiles"]
                ],
            }
            return 201, {"service": service, "deployId": "dep-synthetic"}
        raise AssertionError((method, path))


def service_desired() -> dict:
    return {
        "adapter": "render-private-service",
        "embedding_image": EMBEDDING_IMAGE,
        "region_ref": "approval://provider-region",
        "billing_ref": "approval://provider-billing",
        "public_ingress": False,
        "image": CORE_IMAGE,
        "region": "virginia",
        "billing_plan": "balanced-ha",
    }


def network_desired() -> dict:
    return {
        "adapter": "tailscale-gateway",
        "gateway_image": GATEWAY_IMAGE,
        "route_ref": "approval://tailnet-route",
        "listen_port": 9443,
        "provider_authorized": True,
        "route_approved": True,
    }


class LiveProviderAdapterTest(unittest.TestCase):
    def test_planetscale_create_then_unchanged_is_exact_and_autoscaling_bounded(self):
        provider = FakePlanetScale()
        adapter = PlanetScaleDatabaseAdapter(
            provider,
            organization="synthetic-org",
            database_name="synthetic-recall",
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="us-east",
            cluster_size="PS_80",
            replicas=2,
            major_version="17",
            minimum_storage_bytes=50 * 1024**3,
            maximum_storage_bytes=1024 * 1024**3,
        )
        desired = {
            "adapter": "postgres",
            "provider": "planetscale",
            "url_ref": "secret://runtime/RECALL_DATABASE_URL",
            "tls_mode": "verify-full",
            "region": "virginia",
            "billing_plan": "balanced-ha",
        }
        first = adapter.ensure("recall-core-database", desired)
        second = adapter.ensure("recall-core-database", desired)
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")
        create = [call for call in provider.calls if call[0] == "POST"]
        self.assertEqual(len(create), 1)
        self.assertEqual(
            create[0][2],
            {
                "name": "synthetic-recall",
                "kind": "postgresql",
                "cluster_size": "PS_80",
                "region": "us-east",
                "replicas": 2,
                "major_version": "17",
                "storage": {
                    "minimum_storage_bytes": 50 * 1024**3,
                    "maximum_storage_bytes": 1024 * 1024**3,
                },
            },
        )

    def test_planetscale_drift_fails_without_mutation(self):
        provider = FakePlanetScale()
        provider.database = {
            "id": "db-synthetic-1",
            "name": "synthetic-recall",
            "kind": "mysql",
            "state": "ready",
            "region": {"slug": "us-east"},
        }
        adapter = PlanetScaleDatabaseAdapter(
            provider,
            organization="synthetic-org",
            database_name="synthetic-recall",
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="us-east",
            cluster_size="PS_80",
            replicas=2,
            major_version="17",
            minimum_storage_bytes=50 * 1024**3,
            maximum_storage_bytes=1024 * 1024**3,
        )
        with self.assertRaisesRegex(LiveProviderError, "database_drift"):
            adapter.ensure(
                "recall-core-database",
                {
                    "adapter": "postgres",
                    "provider": "planetscale",
                    "url_ref": "secret://runtime/RECALL_DATABASE_URL",
                    "tls_mode": "verify-full",
                    "region": "virginia",
                    "billing_plan": "balanced-ha",
                },
            )
        self.assertFalse(any(call[0] == "POST" for call in provider.calls))

    def test_render_stack_creates_only_private_digest_pinned_services(self):
        provider = FakeRender()
        context: dict[str, str] = {}
        adapter = RenderPrivateStackAdapter(
            provider,
            context,
            owner_id="owner-synthetic",
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="virginia",
            core_name="synthetic-recall-core",
            embedding_name="synthetic-recall-embedding",
            core_plan="starter",
            embedding_plan="pro",
            database_url=(
                "postgresql://synthetic:synthetic@db.invalid/recall"
                "?sslmode=verify-full&sslrootcert=system"
            ),
        )
        first = adapter.ensure("recall-core-service", service_desired())
        second = adapter.ensure("recall-core-service", service_desired())
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")
        creates = [call[2] for call in provider.calls if call[0] == "POST"]
        self.assertEqual(
            [body["type"] for body in creates],
            [
                "private_service",
                "private_service",
            ],
        )
        self.assertEqual(
            [body["image"]["imagePath"] for body in creates],
            [EMBEDDING_IMAGE, CORE_IMAGE],
        )
        core = creates[1]
        env = {item["key"]: item["value"] for item in core["envVars"]}
        self.assertEqual(env["RECALL_AUTH_REQUIRED"], "1")
        self.assertEqual(
            env["RECALL_EMBEDDING_URL"], env["RECALL_EMBEDDING_APPROVED_URL"]
        )
        self.assertNotIn("public", json.dumps(creates).casefold())
        self.assertIn("core_url", context)

    def test_gateway_is_private_9443_without_funnel_or_routes(self):
        provider = FakeRender()
        context = {"core_url": "http://synthetic-recall-core.internal:10000"}
        adapter = RenderTailscaleGatewayAdapter(
            provider,
            context,
            owner_id="owner-synthetic",
            region="virginia",
            name="synthetic-recall-gateway",
            plan="starter",
            hostname="synthetic-recall",
            tag="tag:synthetic-recall",
            client_id="synthetic-client-id",
            client_secret="synthetic-client-secret",
        )
        first = adapter.ensure("recall-core-network", network_desired())
        second = adapter.ensure("recall-core-network", network_desired())
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")
        creates = [call[2] for call in provider.calls if call[0] == "POST"]
        self.assertEqual(len(creates), 1)
        body = creates[0]
        self.assertEqual(body["type"], "private_service")
        self.assertEqual(body["image"]["imagePath"], GATEWAY_IMAGE)
        files = {item["name"]: item["content"] for item in body["secretFiles"]}
        serve = json.loads(files["serve.json"])
        self.assertEqual(serve["TCP"], {"9443": {"HTTPS": True}})
        self.assertEqual(
            serve["Web"]["${TS_CERT_DOMAIN}:9443"]["Handlers"]["/"]["Proxy"],
            context["core_url"],
        )
        rendered = json.dumps(body).casefold()
        self.assertNotIn("funnel", rendered)
        self.assertNotIn("advertise-routes", rendered)
        self.assertNotIn('"443":', rendered)
        result = json.dumps([first, second])
        self.assertNotIn("synthetic-client-secret", result)
        self.assertNotIn("synthetic-client-id", result)

    def test_existing_render_configuration_drift_fails_closed(self):
        provider = FakeRender()
        context: dict[str, str] = {}
        adapter = RenderPrivateStackAdapter(
            provider,
            context,
            owner_id="owner-synthetic",
            region_selection="virginia",
            billing_selection="balanced-ha",
            region="virginia",
            core_name="synthetic-recall-core",
            embedding_name="synthetic-recall-embedding",
            core_plan="starter",
            embedding_plan="pro",
            database_url=(
                "postgresql://synthetic:synthetic@db.invalid/recall"
                "?sslmode=verify-full&sslrootcert=system"
            ),
        )
        adapter.ensure("recall-core-service", service_desired())
        core = provider.services["synthetic-recall-core"]
        configuration = provider.configuration[core["id"]]["envVars"]
        next(item for item in configuration if item["key"] == "RECALL_AUTH_REQUIRED")[
            "value"
        ] = "0"
        creates_before = len([call for call in provider.calls if call[0] == "POST"])
        with self.assertRaisesRegex(LiveProviderError, "render_configuration_drift"):
            adapter.ensure("recall-core-service", service_desired())
        self.assertEqual(
            len([call for call in provider.calls if call[0] == "POST"]),
            creates_before,
        )


if __name__ == "__main__":
    unittest.main()
