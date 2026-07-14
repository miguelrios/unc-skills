import copy
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/recap/scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load("event_ledger")
accounting_module = load("accounting")
synthesis = load("synthesis")


def event(ordinal: int, surface: str) -> dict:
    text = f"evidence {ordinal}"
    return {
        "ordinal": ordinal,
        "event_id": f"event-{ordinal}",
        "event_native_id": f"native-{ordinal}",
        "item_ordinal": 0,
        "timestamp": float(ordinal),
        "surface": surface,
        "role": surface,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "receipt": None,
    }


def fixture(root: Path):
    builder = ledger.LedgerBuilder(root / "private/manifest.json", heartbeat_every=0)
    for ordinal, surface in enumerate((
        "user", "assistant", "tool_input", "tool_output", "tool_input", "tool_output",
    )):
        builder.add(event(ordinal, surface))
    manifest = {
        "scope": {"harness": "codex"},
        "coverage": {"observed_events": 6, "source_complete": True},
        "ledger": builder.finish(),
        "git": {
            "session_observed": {
                "file_mutations": [{
                    "event_id": "event-4", "path": "/repo/src/change.py",
                    "result": {"event_id": "event-5", "status": "passed"},
                }],
                "observed_commits": [],
                "test_commands": [{
                    "event_id": "event-2",
                    "result": {"event_id": "event-3", "status": "passed"},
                }],
            },
            "verified_now": {"repositories": [{
                "repo_root": "/repo", "available": True, "head": "abc123",
                "branch": "main", "changed_paths": [], "commits": [{"sha": "abc123"}],
            }]},
        },
    }
    draft_accounting = {
        "schema_version": accounting_module.ACCOUNTING_SCHEMA,
        "claims": [
            {"claim_id": "goal", "kind": "goal", "label": "Goal", "event_ids": ["event-0"]},
            {
                "claim_id": "decision", "kind": "decision", "label": "Decision",
                "event_ids": ["event-1"],
            },
            {
                "claim_id": "verify", "kind": "verification", "label": "Verification",
                "event_ids": ["event-2", "event-3"],
            },
            {
                "claim_id": "change", "kind": "change", "label": "Change",
                "event_ids": ["event-4", "event-5"],
            },
        ],
        "low_signal_groups": [],
    }
    accounting, result = accounting_module.seal_accounting(manifest, draft_accounting)
    if not result["valid"]:
        raise AssertionError(result)
    return manifest, accounting


def item(item_id, title, summary, source, claims, events, **extra):
    return {
        "id": item_id,
        "title": title,
        "summary": summary,
        "source_label": source,
        "accounting_claim_ids": claims,
        "evidence_ids": events,
        **extra,
    }


def valid_draft(manifest, accounting):
    return {
        "schema_version": synthesis.SYNTHESIS_SCHEMA,
        "manifest_sha256": accounting_module.canonical_sha256(manifest),
        "accounting_sha256": accounting_module.canonical_sha256(accounting),
        "headline": {
            "id": "headline", "summary": "The requested change was implemented and verified.",
            "source_label": "session_observed",
            "accounting_claim_ids": ["goal", "decision", "verify", "change"],
            "evidence_ids": [f"event-{index}" for index in range(6)],
        },
        "story": [
            item(
                "story-setup", "Goal and approach", "The user set a goal and the agent chose an approach.",
                "session_observed", ["goal", "decision"], ["event-0", "event-1"],
                narrative_role="setup",
            ),
            item(
                "story-outcome", "Proof and outcome", "The check passed and the change was recorded.",
                "session_observed", ["verify", "change"],
                ["event-2", "event-3", "event-4", "event-5"], narrative_role="outcome",
            ),
        ],
        "timeline": [
            item(
                "time-goal", "Goal", "The goal was stated.", "session_observed",
                ["goal"], ["event-0"], first_ordinal=0, last_ordinal=0,
            ),
            item(
                "time-decision", "Decision", "The approach was reported.", "agent_report",
                ["decision"], ["event-1"], first_ordinal=1, last_ordinal=1,
            ),
            item(
                "time-verify", "Verification", "The observed check passed.", "session_observed",
                ["verify"], ["event-2", "event-3"], first_ordinal=2, last_ordinal=3,
            ),
            item(
                "time-change", "Change", "The file mutation and result were observed.",
                "session_observed", ["change"], ["event-4", "event-5"],
                first_ordinal=4, last_ordinal=5,
            ),
        ],
        "changes": [
            item(
                "change-file", "Changed path", "The session changed one file.",
                "session_observed", ["change"], ["event-4", "event-5"],
                paths=["/repo/src/change.py"], commits=[],
            ),
        ],
        "verification": [
            item(
                "verify-pass", "Observed check", "The observed command completed successfully.",
                "session_observed", ["verify"], ["event-2", "event-3"], outcome="passed",
                command_event_id="event-2", result_event_id="event-3",
            ),
        ],
        "failures_recoveries": [],
        "final_state": [
            item(
                "final-clean", "Current worktree", "The worktree is verified clean now.",
                "verified_now", [], [],
                git_evidence=[{"repo_root": "/repo", "kind": "clean", "value": True}],
            ),
        ],
        "open_work": [],
        "coverage": {"low_signal_group_ids": []},
    }


class SynthesisTest(unittest.TestCase):
    def test_valid_story_and_timeline_render_different_accounted_views(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertTrue(result["valid"], result["errors"])
            rendered, receipt = synthesis.render_markdown(manifest, accounting, draft)
            self.assertIn("## Story", rendered)
            self.assertIn("## Timeline", rendered)
            self.assertLessEqual(receipt["word_count"], 2500)

    def test_inference_caveat_is_required_and_rendered(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            draft["open_work"] = [item(
                "inferred-open", "Possible follow-up", "A follow-up may remain.", "inference",
                ["goal"], ["event-0"], caveat="The session does not state this explicitly.",
            )]
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertTrue(result["valid"], result["errors"])
            rendered, _ = synthesis.render_markdown(manifest, accounting, draft)
            self.assertIn("Caveat: The session does not state this explicitly.", rendered)
            del draft["open_work"][0]["caveat"]
            self.assertFalse(synthesis.validate_synthesis(manifest, accounting, draft)["valid"])

    def test_unknown_evidence_and_claims_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            draft["headline"]["evidence_ids"].append("invented")
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertFalse(result["valid"])
            self.assertTrue(any("unsupported event" in error for error in result["errors"]))

    def test_duplicate_and_dropped_story_timeline_claims_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            duplicate = valid_draft(manifest, accounting)
            duplicate["story"][1]["accounting_claim_ids"].append("goal")
            duplicate["story"][1]["evidence_ids"].append("event-0")
            result = synthesis.validate_synthesis(manifest, accounting, duplicate)
            self.assertFalse(result["valid"])
            self.assertIn("story repeats an accounting claim", result["errors"])

            dropped = valid_draft(manifest, accounting)
            dropped["timeline"].pop()
            result = synthesis.validate_synthesis(manifest, accounting, dropped)
            self.assertFalse(result["valid"])
            self.assertIn(
                "timeline does not cover every significant event exactly once", result["errors"],
            )

    def test_story_and_timeline_cannot_be_the_same_partition(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            story = []
            for index, value in enumerate(draft["timeline"]):
                story.append({
                    key: copy.deepcopy(item_value)
                    for key, item_value in value.items()
                    if key not in {"first_ordinal", "last_ordinal"}
                })
                story[-1]["id"] = f"same-story-{index}"
                story[-1]["narrative_role"] = "approach"
            draft["story"] = story
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertFalse(result["valid"])
            self.assertIn(
                "story and timeline use the same grouping instead of different views",
                result["errors"],
            )

    def test_fabricated_test_and_mismatched_outcome_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            fabricated = valid_draft(manifest, accounting)
            fabricated["verification"][0]["command_event_id"] = "invented"
            result = synthesis.validate_synthesis(manifest, accounting, fabricated)
            self.assertFalse(result["valid"])
            self.assertIn("verification references a fabricated test command", result["errors"])

            mismatch = valid_draft(manifest, accounting)
            mismatch["verification"][0]["outcome"] = "failed"
            result = synthesis.validate_synthesis(manifest, accounting, mismatch)
            self.assertFalse(result["valid"])
            self.assertIn(
                "verification outcome does not match observed test evidence", result["errors"],
            )

    def test_current_git_claim_and_unknown_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            draft["final_state"][0]["git_evidence"][0]["value"] = False
            draft["headline"]["unsupported_prose"] = "not part of the contract"
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertFalse(result["valid"])
            self.assertTrue(any("does not match" in error for error in result["errors"]))
            self.assertTrue(any("unsupported fields" in error for error in result["errors"]))

    def test_closed_contract_rejects_top_level_detail_shape_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            draft["unexpected"] = True
            draft["changes"].append("not an item")
            del draft["verification"][0]["result_event_id"]
            result = synthesis.validate_synthesis(manifest, accounting, draft)
            self.assertFalse(result["valid"])
            self.assertIn(
                "synthesis top-level fields are not the closed contract", result["errors"],
            )
            self.assertIn("changes contains a non-object item", result["errors"])
            self.assertTrue(any("missing required fields" in error for error in result["errors"]))

    def test_renderer_fails_closed_above_word_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, accounting = fixture(Path(temporary))
            draft = valid_draft(manifest, accounting)
            verbose = " ".join(["bounded"] * 140)
            for index in range(20):
                draft["final_state"].append(item(
                    f"verbose-{index}", f"Verbose {index}", verbose, "verified_now", [], [],
                    git_evidence=[{"repo_root": "/repo", "kind": "clean", "value": True}],
                ))
            self.assertTrue(synthesis.validate_synthesis(manifest, accounting, draft)["valid"])
            with self.assertRaisesRegex(ValueError, "2,500-word"):
                synthesis.render_markdown(manifest, accounting, draft)

    def test_synthesis_module_has_no_provider_or_slack_client(self):
        source = (SCRIPTS / "synthesis.py").read_text().casefold()
        for forbidden in ("openai", "anthropic", "litellm", "slack", "requests", "httpx"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
