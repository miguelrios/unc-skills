# L1 — Reversible public direct MCP proof · EXIT (2026-07-18)

## Status: AT_BOUND

The complete Recall MCP contract passes against production-scale owner data in the exact
immutable merged image, but the existing public Render service cannot establish its PlanetScale
connection during the temporary IPv4-only ingress window. Public L1 therefore does not claim
completion and L2 does not start.

Cleanup completed before this EXIT: the database allowlist equals its pre-window snapshot, both
anywhere-rule counts are zero, every temporary credential and role is gone, the Render
configuration is restored and suspended, rollback is inactive, and private inputs, credentials,
and raw provider files were destroyed.

## The headline evidence

| Gate | Result |
|---|---:|
| Exact merged-image real-data conformance cells | 62 / 62 |
| Supported protocol versions | 3 / 3 |
| Methods | 5 / 5 |
| Tools | 5 / 5 |
| Capability classes | 3 / 3 |
| Declared arguments | 19 / 19 |
| Lifecycle paths | 16 / 16 |
| Abuse cells | 11 / 11 |
| Private questions with evidence | 5 / 5 |
| Winning receipts resolved | 17 / 17 |
| Capture event delta | 1 |
| Capture replay event delta | 0 |
| Live hits after forget | 0 |
| Additional route/auth/body/origin bounds | 19 / 19 |
| Credential rotation/revocation checks | 4 / 4 |
| Revoked temporary credentials returning 401 | 4 / 4 |
| Credential values or response bodies emitted | 0 |

These passing results were produced loopback-only by the same anonymously pullable immutable
image published from merged HEAD, connected to the production-scale database through verified
TLS and the temporary least-privilege role. They prove the application and database contract
below the Render network boundary; they do not substitute for the missing public Render proof.

## What shipped

| Piece | Where |
|---|---|
| Content-free MCP response bound and bounded receipt resolution | PR #84 |
| Canonical capture/search receipt equality | PR #85 |
| Aggregate-only public and loopback proof harness | L0 PR #82 plus private L1 config |

The first live public run found that an unbounded `recall_show` response could exceed the
one-megabyte harness bound. PR #84 converts overflow into a small content-free JSON-RPC error and
uses bounded show windows in conformance. The next real-data run found that capture returned an
event receipt while search returned its canonical item receipt. PR #85 makes capture return that
same resolvable item receipt and pins the equality in a fresh-PostgreSQL MCP E2E.

## The hard boundary

After both fixes merged and their exact image was published:

- the image passed its production database capability check outside Render;
- the exact Render database configuration passed verified TLS, schema 16, and least-privilege
  capability checks outside Render;
- the exact image plus that exact configuration passed together outside Render;
- Render repeatedly exited with the content-free provider code `database_connection_failed`;
- explicit IPv4 host selection did not change the Render result;
- PlanetScale accepted IPv4 CIDRs only, so IPv6 remained closed as required;
- no second public MCP conformance run started.

This isolates the unproved edge to Render outbound connectivity during PlanetScale IP
restriction enforcement. Resolving it requires a new network decision, not another application
retry.

## Bound accounting

- Failed product-evidence runs: 2 / 2. The public run exposed the response bound; the
  below-boundary real-data run exposed capture/search receipt inequality. Both produced merged
  TDD fixes. The final below-boundary rerun passed 62/62.
- REVIEW→fix rounds: 2 / 3. PRs #84 and #85 both passed required CI with no review findings.
- Instrument failures: 5. A direct registry push lacked package-publish permission; preliminary
  short-lived roles were misconfigured and deleted before ingress; the first Render
  resume-and-deploy raced and restored safely; an IPv6 rule request was rejected without changing
  the IPv4-only state; and repeated Render startup probes ended at the network boundary. None
  emitted private content or bypassed restore-first handling.
- Relaxed-ingress windows: each remained below the 90-minute bound and had an independently armed
  restore. Both ended with exact restoration.

## Accept criteria → evidence

1. **The code-derived live matrix passes 100% with zero unexplained cell and zero emitted
   credential value or response body.** — ❌ Public Render did not reach the final matrix.
   Below the boundary, the exact merged image passed 62/62 with both emission counts zero.
2. **Five private questions return evidence and every winning receipt resolves.** — ⚠️ The
   below-boundary exact-image run returned evidence for 5/5 and resolved 17/17 receipts. The final
   public rerun could not start.
3. **Capture retry creates exactly one canonical event, forget leaves zero live hit, rotation
   succeeds, and the revoked token returns 401 immediately.** — ✅ The exact-image run observed
   event delta one, replay delta zero, zero post-forget hit, successful replacement, and immediate
   401 from the revoked credential.
4. **Authorization escapes, cross-source mutations, unexpected public routes, IPv6-anywhere
   rules, and secret/public-data findings are all zero.** — ✅ The 62-cell conformance and
   19-check boundary matrix passed; IPv6-anywhere remained zero; both implementation secret scans
   and CI passed.
5. **Filtered fast related returns within 15 seconds; rollback remains armed and the window stays
   below 90 minutes.** — ⚠️ Fast related passed its bounded application check and every ingress
   window stayed below 90 minutes with independent rollback. No public final-run latency claim is
   made because Render never became ready.
6. **Deployed code equals merged HEAD; a content-free EXIT is merged and verified at HEAD.** —
   ❌ The immutable image equals merged HEAD, but it did not become live on Render. This EXIT is
   the final L1 artifact and deliberately records `AT_BOUND`.

## Cleanup proof

| Cleanup invariant | Result |
|---|---:|
| PlanetScale allowlist equals pre-window snapshot | yes |
| IPv4-anywhere rules | 0 |
| IPv6-anywhere rules | 0 |
| Temporary role IDs present | 0 |
| Active temporary MCP credentials | 0 |
| Render image and environment equal pre-window state | yes |
| Render service suspended | yes |
| Rollback active | no |
| Local proof containers present | 0 |
| Private credential/query/raw-provider files retained | 0 |

## The running delta table

| Loop | Shipped | Headline |
|---|---|---|
| Predecessor T1 | Direct temporary-ingress probe | 66/73 cells; 5/5 questions; receipt resolution 0.00 |
| L0 | Contract truth + reusable conformance gate | 61/61 synthetic cells; 6/6 mutants rejected |
| L1 | Two product fixes + exact-image real-data proof | 62/62 below boundary; 5/5 questions; 17/17 receipts; public Render AT_BOUND |

## ZEN and drift check

- **Simple:** two falsified contracts became two small general fixes.
- **General:** response bounds and canonical receipts apply to every MCP host, not one deployment.
- **Agentic:** the runner judges tool behavior while private questions and content remain outside
  public artifacts.
- **Beautiful:** the same aggregate gate distinguishes application truth from hosting truth.
- **Dope:** the complete real-data lifecycle now passes in one immutable image.
- **Drift:** none. L1 stopped at the declared public-network boundary, restored every temporary
  mutation, destroyed sensitive proof inputs, and did not attempt Grep or purchase infrastructure.

## exit → human network gate

Do not start L2. The owner must choose one new chain:

1. add dedicated/static Render outbound IPv4 and allowlist only those addresses;
2. move the public MCP control plane to a host with stable outbound IPv4; or
3. pause public hosting and keep Recall private.

The smallest continuation on the current architecture is dedicated Render egress followed by one
fresh L1 public proof. The successful 62-cell application result must not be rerun until that
network boundary changes.
