from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

from contracts.v2 import (
    ContractError,
    VALIDATORS,
    validate_contract,
    validate_retrieval_exchange,
)


RECALL = Path(__file__).resolve().parents[1]
CATALOG = RECALL / "contracts/recall_v2_boundary_v1.json"
EXAMPLES = RECALL / "contracts/examples/recall_v2_boundary_v1.json"


def examples() -> dict[str, dict]:
    values = json.loads(EXAMPLES.read_text())
    return {value["contract"]: value for value in values}


class V2BoundaryCatalogTest(unittest.TestCase):
    def test_catalog_is_closed_versioned_and_matches_runtime(self) -> None:
        catalog = json.loads(CATALOG.read_text())
        definitions = catalog["$defs"]
        contract_names = {
            definition["properties"]["contract"]["const"]
            for definition in definitions.values()
            if isinstance(definition, dict)
            and isinstance(definition.get("properties"), dict)
            and isinstance(definition["properties"].get("contract"), dict)
        }
        self.assertEqual(contract_names, set(VALIDATORS))
        self.assertEqual(len(catalog["oneOf"]), len(VALIDATORS))
        for name in (
            "principal_authority",
            "artifact_ref",
            "canonical_document",
            "canonical_chunk",
            "ingest_job",
            "forget_request",
            "receipt_redirect",
            "retrieval_request",
            "retrieval_result",
            "mcp_principal",
            "model_payload",
            "public_evidence",
        ):
            self.assertFalse(definitions[name]["additionalProperties"])
            self.assertEqual(definitions[name]["properties"]["schema_version"]["const"], 1)

    def test_all_deterministic_examples_validate_without_mutation(self) -> None:
        values = json.loads(EXAMPLES.read_text())
        self.assertEqual(len(values), len(VALIDATORS))
        self.assertEqual(len({value["contract"] for value in values}), len(values))
        for value in values:
            before = copy.deepcopy(value)
            self.assertEqual(validate_contract(value), value)
            self.assertEqual(value, before)

    def test_every_contract_rejects_unknown_fields_and_nonfinite_json(self) -> None:
        for value in examples().values():
            mutant = copy.deepcopy(value)
            mutant["raw"] = "must fail"
            with self.assertRaises(ContractError):
                validate_contract(mutant)
        evidence = copy.deepcopy(examples()["recall.public-evidence.v1"])
        evidence["metrics"]["nan"] = math.nan
        with self.assertRaisesRegex(ContractError, "finite JSON"):
            validate_contract(evidence)


class V2AuthorityAndStorageSafetyTest(unittest.TestCase):
    def test_source_writes_are_exactly_scoped(self) -> None:
        authority = copy.deepcopy(examples()["recall.principal-authority.v1"])
        authority["source_ids"] = []
        with self.assertRaisesRegex(ContractError, "has no sources"):
            validate_contract(authority)

        authority = copy.deepcopy(examples()["recall.principal-authority.v1"])
        authority["scopes"] = ["recall:archive:write", "recall:ingest"]
        with self.assertRaisesRegex(ContractError, "must be separate"):
            validate_contract(authority)

        authority["scopes"] = ["recall:archive:write"]
        validate_contract(authority)

    def test_archive_reference_rejects_urls_traversal_and_encryption_drift(self) -> None:
        artifact = examples()["recall.artifact-ref.v1"]
        for object_key in (
            "https://archive.example.test/object",
            "../private/object",
            "/objects/01/0123456789abcdef0123456789abcdef",
        ):
            mutant = copy.deepcopy(artifact)
            mutant["object_key"] = object_key
            with self.assertRaisesRegex(ContractError, "not opaque"):
                validate_contract(mutant)
        mutant = copy.deepcopy(artifact)
        mutant["storage_backend"] = "filesystem"
        with self.assertRaisesRegex(ContractError, "filesystem artifact encryption"):
            validate_contract(mutant)

    def test_document_and_chunk_pin_redacted_text_and_source_lineage(self) -> None:
        document = copy.deepcopy(examples()["recall.canonical-document.v1"])
        document["text_redacted"] = "changed after digest"
        with self.assertRaisesRegex(ContractError, "digest does not match"):
            validate_contract(document)
        document = copy.deepcopy(examples()["recall.canonical-document.v1"])
        document["deleted_at"] = "2026-07-19T01:00:00Z"
        with self.assertRaisesRegex(ContractError, "cannot be current"):
            validate_contract(document)
        chunk = copy.deepcopy(examples()["recall.canonical-chunk.v1"])
        chunk["receipt"] = "recall://synthetic:other/item-1?rev=1#item=0"
        with self.assertRaisesRegex(ContractError, "source mismatch"):
            validate_contract(chunk)

    def test_delete_redirect_and_job_states_are_explicit(self) -> None:
        forget = copy.deepcopy(examples()["recall.forget-request.v1"])
        forget["target_receipt"] = "recall://synthetic:other/item-1?rev=1#item=0"
        with self.assertRaisesRegex(ContractError, "target source mismatch"):
            validate_contract(forget)
        redirect = copy.deepcopy(examples()["recall.receipt-redirect.v1"])
        redirect["new_receipt"] = redirect["old_receipt"]
        with self.assertRaisesRegex(ContractError, "lineage is invalid"):
            validate_contract(redirect)
        job = copy.deepcopy(examples()["recall.ingest-job.v1"])
        job["error_code"] = "provider_error"
        with self.assertRaisesRegex(ContractError, "successful job"):
            validate_contract(job)


class V2RetrievalAndEvidenceSafetyTest(unittest.TestCase):
    def test_retrieval_exchange_rejects_tenant_source_and_scope_escape(self) -> None:
        values = examples()
        authority = copy.deepcopy(values["recall.principal-authority.v1"])
        request = copy.deepcopy(values["recall.retrieval-request.v1"])
        result = copy.deepcopy(values["recall.retrieval-result.v1"])
        validate_retrieval_exchange(authority, request, result)

        wrong_tenant = copy.deepcopy(result)
        wrong_tenant["tenant_id"] = "tenant:other"
        with self.assertRaisesRegex(ContractError, "authority mismatch"):
            validate_retrieval_exchange(authority, request, wrong_tenant)

        wrong_source = copy.deepcopy(result)
        item = wrong_source["results"][0]
        item["source_id"] = "synthetic:other"
        item["receipt"] = "recall://synthetic:other/item-1?rev=1#item=0"
        with self.assertRaisesRegex(ContractError, "source scope mismatch"):
            validate_retrieval_exchange(authority, request, wrong_source)

        no_search = copy.deepcopy(authority)
        no_search["scopes"] = ["recall:status"]
        with self.assertRaisesRegex(ContractError, "scope is missing"):
            validate_retrieval_exchange(no_search, request, result)

    def test_mcp_write_scope_binds_exactly_one_source(self) -> None:
        principal = copy.deepcopy(examples()["recall.mcp-principal.v1"])
        principal["scopes"].append("recall:forget")
        principal["source_ids"].append("synthetic:other")
        with self.assertRaisesRegex(ContractError, "bind one source"):
            validate_contract(principal)

    def test_model_boundary_rejects_raw_artifacts_and_digest_drift(self) -> None:
        payload = copy.deepcopy(examples()["recall.model-payload.v1"])
        payload["raw_artifact_ids"] = ["art_0123456789abcdef"]
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_contract(payload)
        payload = copy.deepcopy(examples()["recall.model-payload.v1"])
        payload["texts"][0]["text_redacted"] = "unverified text"
        with self.assertRaisesRegex(ContractError, "digest does not match"):
            validate_contract(payload)

    def test_public_evidence_is_aggregate_only(self) -> None:
        evidence = examples()["recall.public-evidence.v1"]
        validate_contract(evidence)
        for key in ("query", "answer", "transcript", "path", "token", "trace"):
            mutant = copy.deepcopy(evidence)
            mutant["metrics"][key] = {"count": 1}
            with self.assertRaisesRegex(ContractError, "content-bearing"):
                validate_contract(mutant)
        mutant = copy.deepcopy(evidence)
        mutant["metrics"]["note"] = "looks good"
        with self.assertRaisesRegex(ContractError, "aggregates only"):
            validate_contract(mutant)
        mutant = copy.deepcopy(evidence)
        nested = mutant["metrics"]
        for index in range(10):
            nested[f"level_{index}"] = {}
            nested = nested[f"level_{index}"]
        with self.assertRaisesRegex(ContractError, "depth bound"):
            validate_contract(mutant)

    def test_contracts_have_a_global_byte_bound(self) -> None:
        payload = copy.deepcopy(examples()["recall.retrieval-request.v1"])
        payload["query"] = "x" * 4_000_000
        with self.assertRaisesRegex(ContractError, "byte bound"):
            validate_contract(payload)


if __name__ == "__main__":
    unittest.main()
