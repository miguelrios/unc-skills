# L0 — Contract truth and conformance harness · EXIT (2026-07-18)

## Status: COMPLETE

The public MCP schemas now match runtime behavior, and the reusable conformance runner closes
every code-derived coverage cell without publishing response content.

## The headline evidence

| Synthetic gate | Result |
|---|---:|
| Code-derived conformance cells | 61 / 61 |
| Supported protocol versions | 3 / 3 |
| Methods | 5 / 5 |
| Tools | 5 / 5 |
| Capability classes | 3 / 3 |
| Declared arguments | 19 / 19 |
| Lifecycle paths | 16 / 16 |
| Abuse cells | 11 / 11 |
| Fake-success mutants rejected | 6 / 6 |
| Recall Python suite | 386 passed |
| Recall Node suite | 3 passed |

The loopback proof used synthetic inputs. Its persisted report is a closed aggregate-only schema:
no credential value, query, receipt, response body, endpoint, or path is an allowed output field.

## What shipped

| Piece | Where |
|---|---|
| Truthful MCP argument schemas and pre-store runtime bounds | PR #81 |
| Closed, private-file MCP conformance runner and CLI | PR #82 |
| Code-derived manifest, loopback E2E, redirect/file safety, and mutant pins | PR #82 |

## Bound accounting (honest)

- PROVE evidence failures: 0 / 2.
- REVIEW→fix rounds: 0 / 3; both implementation PRs had green required CI and no review
  findings.
- Instrument failures: 1. The first combined targeted command omitted the package import path;
  the 80 tests it loaded passed, and the missing test module did not load. The corrected command
  loaded and passed all 90 targeted tests. This did not exercise a failing product claim and did
  not consume the PROVE bound.

## Accept criteria → evidence

1. **Schema and runtime agree on show timestamps, mutual exclusion, search/related limits, query
   length, capture size, provenance, and transport behavior; invalid inputs fail before store
   mutation.** — ✅ PR #81 merged after 71 targeted contract tests, the full gate, staged secret
   scanning, and required CI passed.
2. **The runner maps 100% of supported protocol versions, methods, tools, capability classes,
   declared arguments, lifecycle paths, and named abuse cells; each fake-success mutant fails.**
   — ✅ The code-derived 61-cell manifest passed 61/61. The omitted-case, swallowed-error,
   unresolved-receipt, duplicate-capture, ineffective-forget, and leaked-body mutants each failed
   validation in PR #82.
3. **A synthetic loopback end-to-end matrix passes every cell, including capture replay and
   forget, while emitting zero credential values or response bodies.** — ✅ The real HTTP
   loopback passed all 61 cells. It observed one live canonical result after capture, no increase
   after replay, and zero live result after forget; its aggregate output schema excludes private
   strings.
4. **The full Recall suite, staged secret scan, and CI pass; both implementation PRs and this
   loop's content-free EXIT are merged and verified at HEAD.** — ✅ PRs #81 and #82 are merged.
   Their required CI passed; the final local gate passed 386 Python and 3 Node tests; both staged
   secret scans reported zero findings. This EXIT is the final L0 merge artifact.

## The running delta table (L0→Ln)

| Loop | Shipped | Headline |
|---|---|---|
| Predecessor T1 | Direct temporary-ingress probe | 66/73 direct cells; 5/5 private questions; receipt resolution 0.00 |
| L0 | Contract truth + reusable conformance gate | 61/61 synthetic cells; 6/6 mutants rejected; 386+3 regression tests |

The synthetic 61-cell result is a new deterministic gate, not a claim that the predecessor's
seven live failures disappeared. L1 must measure that live delta through the same runner.

## ZEN and drift check

- **Simple:** one closed config and one aggregate output.
- **General:** coverage derives from shipped MCP definitions rather than a one-off deployment
  diary.
- **Agentic:** private questions remain owner-selected data; the harness judges structural
  protocol truth and lifecycle outcomes.
- **Beautiful:** omission and fake success are explicit test failures.
- **Dope:** capture, replay, forget, isolation, abuse, and receipt resolution are one reusable
  command.
- **Drift:** none. This loop changed MCP contract truth and its proof mechanism only; it did not
  open ingress, touch production data, rotate credentials, or alter infrastructure.

## exit → L1 (reversible public direct MCP proof)

L1 starts only after this EXIT is merged. It must use the merged runner, an independently tested
rollback, a window below 90 minutes, aggregate-only public evidence, and unconditional
restore-first cleanup.
