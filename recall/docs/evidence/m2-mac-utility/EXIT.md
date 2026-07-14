# M2 — ONE MAC UTILITY · EXIT (2026-07-14)

## Status: COMPLETE

## The headline evidence

One reproducible arm64 Mac bundle now packages Claude Code, Codex/ChatGPT Mac, Claude Cowork,
and ChatGPT export-inbox ingestion behind the `recall-brain` utility. Two production-runtime
builds are byte-identical. Sixteen focused tests and all 193 repository tests pass. An isolated
synthetic-home lifecycle on the authorized Mac proved legacy upgrade, checkpoint retention,
source-scoped authority, per-source disable, and both uninstall choices while reading zero private
source records.

## What shipped

| Piece | Where |
|---|---|
| Cowork sync command through shared privacy/ACK runner | `client/cli.py` |
| Content-free Mac health and per-source disable | `client/macos_utility.py` |
| Unified source installer and state-preserving lifecycle | `client/macos/install.sh`, `client/macos/uninstall.sh` |
| Complete reproducible package payload | `scripts/build_macos_package.py` |
| Explicit local-log consent boundary | `skills/recall/SKILL.md` |
| Synthetic lifecycle and adversarial privacy pins | `tests/test_macos_utility.py`, `tests/test_mac_package.py` |
| Content-free aggregate evidence | `docs/evidence/m2-mac-utility/attestation.json` |

## Bound accounting (honest)

- PROVE runs used: 1 evidence-bearing full repository run, passed.
- Expected RED run: Mac utility module and Cowork package payload absent, then GREEN.
- Instrument failures: 0.
- Review/fix rounds used: 1; review caught and fixed a misplaced installer heredoc plus visibility
  argument duplication before the evidence-bearing proof.

## Accept criteria → evidence

1. One reproducible tarball contains every collector and pinned runtime — ✅ package manifest pins
   Claude Code/Codex collector code, Cowork, export inbox, lifecycle code, and arm64 CPython 3.12.13;
   `test_reproducible_content_free_package_and_clean_install_uninstall` passes.
2. Install, upgrade, per-source disable, and uninstall pass on synthetic macOS paths — ✅ isolated
   Mac lifecycle attestation records every transition, including retain and explicit-delete modes.
3. LaunchAgents use absolute bundled runtimes and source-scoped Keychain accounts — ✅ four of
   four plists passed exact runtime, account/source identity, and unique-source assertions.
4. Status is content-free under adversarial paths, corrupt state, and credentials — ✅
   `test_status_is_closed_content_free_and_reports_health_lag_checkpoint` and
   `test_status_maps_symlink_corruption_and_private_errors_to_closed_codes`.
5. Existing two-source installs upgrade without duplicate agents or lost checkpoints — ✅ the Mac
   lifecycle began with the legacy `claude,codex` selection, upgraded to four unique agents, and
   preserved the exact synthetic checkpoint.
6. Consecutive builds are byte-identical and package scan is empty — ✅ both artifacts have the
   SHA-256 in `attestation.json`; application payload scan found zero credential, transcript,
   private-path, database, or JSONL evidence matches.
7. Every criterion maps to evidence at HEAD — ✅ this document and the referenced tests/attestation.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Closed Cowork selection/identity contract | 15 synthetic cases; 4 focused + 185 full tests green |
| M1 | Bounded Cowork adapter through privacy/ACK runner | 8 focused + 189 full tests; replay 0, append 1, tombstones 0 |
| M2 | Unified reproducible Mac utility | 16 focused + 193 full tests; 2 identical builds; 2→4 source upgrade |

## ZEN check

- **Simple:** one binary, one package, four explicit source classes.
- **General:** source-scoped authority, privacy-before-spool, ACK checkpoints, and content-free
  health apply across local logs and exports.
- **Prompt/agentic-oriented:** the utility owns mechanical safety and lifecycle facts; semantic
  privacy stays in the shared policy layer.
- **Beautiful:** install, status, disable, upgrade, and uninstall form one closed lifecycle with an
  explicit state-retention choice.
- **Dope:** the same small Mac utility can continuously feed a private cross-device brain from the
  work surfaces the user actually selected.

## exit → M3

Deploy the exact reproduced artifact to the authorized Mac, enable the selected real sources under
`scrub`, and prove synthetic canary ingest/search/replay/archive/delete across devices without
publishing any private transcript or host evidence.
