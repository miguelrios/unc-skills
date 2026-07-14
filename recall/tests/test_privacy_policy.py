from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from privacy.policy import AgenticJudge, PrivacyPolicy


CORPUS = Path(__file__).with_name("privacy_eval_v1") / "corpus.jsonl"
MANIFEST = CORPUS.with_name("manifest.json")


def corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line]


class FrozenPrivacyEvalTest(unittest.TestCase):
    def test_manifest_freezes_corpus_and_thresholds(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        rows = corpus()
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        self.assertEqual(manifest["counts"], {
            "benign": sum(not row["sensitive"] for row in rows),
            "sensitive": sum(row["sensitive"] for row in rows),
            "total": len(rows),
        })
        self.assertEqual(set(manifest["thresholds"].values()), {0, 100})

    def test_frozen_thresholds_for_drop_scrub_and_benign_retention(self) -> None:
        rows = corpus()

        def judge(text: str) -> list[dict]:
            for row in rows:
                if row.get("judge_spans") and text == row["input"]["text"]:
                    return row["judge_spans"]
            return []

        scrub = PrivacyPolicy(mode="scrub", judge=judge)
        drop = PrivacyPolicy(mode="drop", judge=judge)
        sensitive = [row for row in rows if row["sensitive"]]
        benign = [row for row in rows if not row["sensitive"]]
        scrubbed = [scrub.apply(row["input"]) for row in rows]
        dropped = [drop.apply(row["input"]) for row in rows]

        self.assertTrue(all(decision.action == "scrub" for decision in scrubbed[: len(sensitive)]))
        self.assertTrue(all(decision.action == "drop" and decision.value is None for decision in dropped[: len(sensitive)]))
        self.assertTrue(all(decision.action == "keep" for decision in scrubbed[len(sensitive) :]))
        self.assertTrue(all(decision.value == row["input"] for decision, row in zip(scrubbed[len(sensitive) :], benign, strict=True)))
        for row, decision in zip(sensitive, scrubbed[: len(sensitive)], strict=True):
            rendered = json.dumps(decision.value, sort_keys=True)
            self.assertNotIn(row["input"].get("text", "__not_a_string__"), rendered)
            without_tags = re.sub(r"\[REDACTED:[a-z_]+\]", "", decision.value.get("text", rendered))
            self.assertIn(row["safe_text"], without_tags)

    def test_decision_receipt_is_content_free(self) -> None:
        canary = "synthetic-secret-canary-receipt-77"
        decision = PrivacyPolicy(mode="drop").apply({"text": f"api_key={canary}"})
        rendered = json.dumps(decision.receipt(), sort_keys=True)
        self.assertEqual(set(decision.receipt()), {"action", "categories", "policy_version", "reason_code"})
        self.assertNotIn(canary, rendered)
        self.assertNotIn("text", rendered)

    def test_off_is_byte_semantics_preserving(self) -> None:
        value = {"nested": ["api_key=left-alone-in-explicit-off", {"phone": "+1 202-555-0104"}]}
        decision = PrivacyPolicy(mode="off").apply(value)
        self.assertEqual(decision.action, "keep")
        self.assertIs(decision.value, value)

    def test_agent_failure_obeys_fail_closed_drop_without_echoing_input(self) -> None:
        canary = "contextual-canary-must-not-escape"

        def unavailable(_text: str) -> list[dict]:
            raise OSError("synthetic transport unavailable")

        decision = PrivacyPolicy(mode="scrub", judge=unavailable, judge_failure="drop").apply({"text": canary})
        self.assertEqual(decision.action, "drop")
        self.assertEqual(decision.reason_code, "judge_unavailable")
        self.assertNotIn(canary, json.dumps(decision.receipt()))


class AgenticJudgeContractTest(unittest.TestCase):
    def test_staging_router_schema_and_ephemeral_transport(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "spans": [{"start": 4, "end": 15, "category": "contextual_name"}]
            })}}]
        }).encode()
        judge = AgenticJudge(
            base_url="https://litellm.staging.example.invalid",
            virtual_key="synthetic-scoped-virtual-key",
            model="privacy-judge",
        )
        with mock.patch("urllib.request.urlopen", return_value=response) as opened:
            spans = judge("ask Ada Example")
        self.assertEqual(spans, [{"start": 4, "end": 15, "category": "contextual_name"}])
        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/chat/completions"))
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-scoped-virtual-key")
        self.assertNotIn("synthetic-scoped-virtual-key", request.data.decode())

    def test_direct_provider_and_invalid_span_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "staging LiteLLM"):
            AgenticJudge(base_url="https://api.openai.com", virtual_key="scoped", model="judge")
        judge = AgenticJudge(
            base_url="https://litellm.staging.example.invalid",
            virtual_key="scoped",
            model="judge",
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "{\"spans\":[{\"start\":9,\"end\":2,\"category\":\"name\"}]}"}}]
        }).encode()
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "span"):
                judge("synthetic")

    def test_scoped_key_file_requires_private_mode_scope_and_expiry(self) -> None:
        from privacy.policy import load_scoped_virtual_key

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "judge-key.json"
            path.write_text(json.dumps({
                "virtual_key": "synthetic-scoped-key",
                "scope": "recall-privacy-judge",
                "expires_at": "2099-01-01T00:00:00Z",
            }))
            path.chmod(0o600)
            self.assertEqual(load_scoped_virtual_key(path), "synthetic-scoped-key")
            path.chmod(0o644)
            with self.assertRaisesRegex(PermissionError, "private"):
                load_scoped_virtual_key(path)


if __name__ == "__main__":
    unittest.main()
