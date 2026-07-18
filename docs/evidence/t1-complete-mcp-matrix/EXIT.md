# T1 — Complete MCP protocol, tool, and abuse matrix · EXIT (2026-07-18)

## Status: AT_BOUND

T1 used both allowed live evidence attempts and did not satisfy the complete MCP matrix. The safe
result is an honest stop, not a partial completion. The temporary PlanetScale ingress exception has
already been removed and all temporary credentials and database roles have been revoked or deleted.

## Headline evidence

`attestation.json` contains only aggregate/content-free proof. The final direct matrix passed 66 of
73 cases. All tested protocol, capability, authorization, surface, transport, method, and argument
cells passed. Five private retrieval questions all returned evidence, but receipt resolution was
0.00 because the live run exposed a real `recall_show.around` type mismatch. No private question,
answer, receipt, endpoint, credential, infrastructure identifier, or local path is present here.

## What shipped

| Piece | Evidence |
|---|---|
| Bounded related lookup | PR #77, merged at `20c5a0885f9502bdeada2eaf1a130fa810e2059c` |
| Full Recall test suite | 374 Python and 3 Node tests passed |
| Production-scale related proof | Live MCP latency fell from more than 120 seconds to 6.711 seconds |
| Temporary ingress cleanup | Original allowlist digest restored; rollback timer inactive |
| Credential cleanup | Three temporary MCP credentials revoked; two temporary database roles deleted |

## Bound accounting

- Failed live PROVE runs: 2 of 2.
  1. `recall_related` with cwd/branch exceeded 90 seconds; an isolated probe exceeded 120 seconds.
     PR #77 fixed the query and the exact live MCP path then passed in 6.711 seconds.
  2. The complete direct matrix passed 66/73 cells but exposed a separate
     `recall_show.around` contract mismatch, leaving valid receipt resolution at 0.00.
- Instrument failures excluded from the evidence bound:
  1. The first harness timeout was too short to distinguish ordinary semantic latency.
  2. Two structured-result assertions dereferenced failed results instead of recording them.
  3. Capture/forget fixtures used a provenance scheme outside the intentionally closed production
     allowlist; those four capture cells and dependent forget cells are invalid evidence.
- Review rounds: one for PR #77; CI and the full local suite passed.

## Accept criteria → evidence

1. Complete code-derived matrix with zero unexplained result — ❌ `attestation.json` records 73
   direct cases and complete passing coverage for the deterministic protocol/security categories,
   but seven cells failed and the Grep leg was not reached.
2. All valid tools, receipt resolution, capture/forget lifecycle, rotation, and revocation — ❌
   related was repaired and temporary tokens were revoked, but receipt resolution failed, capture
   fixtures were invalid, and rotation was not proven.
3. Zero authorization escapes and closed bounds — ✅ all six authorization cells, seven public
   surface cells, seven transport cells, six protocol cells, and 24 argument cells passed with zero
   cross-principal read or cross-source mutation.
4. Real Grep sandbox completes the lifecycle — ❌ not run after the direct matrix consumed the
   evidence bound.
5. Zero credential presence in all Grep sandbox surfaces — ❌ no Recall credential was placed in a
   Grep sandbox, but the required five-surface presence audit was not run.
6. Private questions meet the floor with receipt resolution 1.00 — ❌ all five questions returned
   evidence, but receipt resolution was 0.00.

## Mandatory rollback and residue proof

- PlanetScale allowlist equals the pre-experiment snapshot digest
  `374b9fe6edeb051b697c4ab0a949ab3b3cabb96609016c161a6c91fb413b4cc1`.
- The independent rollback timer is inactive.
- Temporary IPv4-anywhere and IPv6-anywhere rules are absent.
- Three temporary MCP credentials exist only as revoked database records; active count is zero.
- Both temporary PlanetScale roles are absent.
- Temporary credential files were destroyed after verification.
- The public service health process remains up while database readiness fails, proving the restored
  network boundary blocks its database path.

## Running delta table

| Loop | Shipped | Headline |
|---|---|---|
| T0 | Reversible public deployment | Live/ready, 5 MCP tools, 0 exposed application routes |
| T1 | Related-query production fix; matrix stopped | 66/73 direct cells; 5/5 retrieval hits; receipt resolution 0.00 |

## ZEN

The production fix is small and general: it uses existing relational identities and gives
`fast=true` an explicit bounded meaning. The chain did not drift into weakening schemas, hiding
failures, or extending public database ingress. The honest next unit is one successor loop that
aligns `recall_show.around`, corrects the closed capture fixture, and reruns the complete matrix from
a restored network posture.

## exit → STOP

T1 is `AT_BOUND`. Do not advance to T2 or reopen ingress under this chain. Create a successor chain
with a fresh evidence bound after owner direction.
