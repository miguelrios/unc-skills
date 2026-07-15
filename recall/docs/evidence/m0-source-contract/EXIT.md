# M0 — SOURCE CONTRACT AND BASELINE · EXIT (2026-07-14)

## Status: COMPLETE

## The headline evidence

The frozen 15-case synthetic corpus yields exactly five eligible natural-language records,
nine excluded records, and one closed contract error. Four focused contract tests and all 185
repository tests pass. No private source was read to produce repository evidence.

## What shipped

| Piece | Where |
|---|---|
| Cowork allowlist projection contract | `connectors/cowork_local.py` |
| Frozen synthetic corpus and policy | `tests/cowork_local_v1/` |
| Fake-success and lifecycle pins | `tests/test_cowork_local.py` |
| Content-free baseline | `docs/evidence/m0-source-contract/baseline.json` |

## Bound accounting (honest)

- PROVE runs used: 1 evidence-bearing full run, passed.
- Expected RED run: missing `connectors.cowork_local`, then GREEN after implementation.
- Instrument failures: 0.
- Review rounds used: 0 before PR review.

## Accept criteria → evidence

1. Synthetic cases cover user, assistant, tool/system, attachment, archive, replay, malformed,
   privacy, and additive-schema behavior — ✅ frozen corpus plus
   `test_manifest_freezes_synthetic_corpus_and_closed_policy`.
2. Excluded classes must produce zero connector records — ✅
   `test_projection_matches_allowlist_and_never_copies_excluded_surfaces`.
3. Native identity is stable across replay and independent of paths — ✅
   `test_native_identity_is_path_independent_replay_stable_and_parented`.
4. Baseline behavior is recorded content-free — ✅ `baseline.json` records 181 baseline tests and
   the 185-test M0 suite; `npm test` passed.
5. Every criterion maps to evidence at HEAD — ✅ this document and the referenced frozen tests.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Closed Cowork selection/identity contract | 15 synthetic cases; 4 focused + 185 full tests green |

## exit → M1

Implement the ACK-cursor local Cowork adapter without inferred deletion, using this projection as
the only content-selection boundary.
