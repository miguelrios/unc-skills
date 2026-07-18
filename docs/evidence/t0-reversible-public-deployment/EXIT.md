# T0 — Reversible public deployment · EXIT (2026-07-18)

## Status: COMPLETE

The reviewed public MCP image is live on Render through a temporary IPv4-only PlanetScale
exception. The exception has an independently tested, persistent 105-minute rollback timer.
Verified TLS, schema 16, least-privilege SQL, bearer authentication, principal scoping, the
MCP-only route profile, and privacy controls remain intact.

## The headline evidence

`attestation.json` records a content-free live proof: readiness `200`, unauthenticated MCP `401`,
six non-MCP routes `404`, schema 16, verified TLS, least-privilege runtime, exact merged image,
successful MCP initialization, and five capability-authorized tools. The original allowlist
snapshot is represented only by its SHA-256 digest.

## What shipped

| Piece | Where |
|---|---|
| Temporary-ingress successor chain | PR #72 |
| Current Render `envSpecificDetails` contract | PR #73 |
| Content-free database probe classifications | PR #74 |
| Full Render image start command | PR #75 |
| Immutable deployed image | `attestation.json` |

## Bound accounting

- Evidence PROVE runs used: 1.
- Instrument failures, not evidence failures:
  1. Render Starter required billing information; the bounded proof moved to the free tier without
     changing the security contract.
  2. Render accepted an obsolete top-level image command but silently dropped it; PR #73 pinned the
     current nested API contract.
  3. The database probe collapsed safe failure classes; PR #74 added structural, content-free codes.
  4. Render replaces the image entrypoint for its command override; PR #75 now supplies the complete
     executable command.
- Review rounds: one CI/review pass per PR; all green.

## Accept criteria → evidence

1. Independent rollback armed and tested before relaxation — ✅ `attestation.json` records a passed
   no-op restore, persistent timer, 105-minute expiry, private snapshot digest, and zero credential
   material.
2. Defenses unchanged except one IPv4-anywhere rule — ✅ database and network sections record
   verified TLS, schema 16, least privilege, capability readiness, one temporary IPv4 rule, and zero
   IPv6-anywhere rule.
3. Reviewed public service with closed routes — ✅ deployment and public-surface sections record one
   managed-HTTPS web service, direct Voyage, no Tailscale or embedding sidecar, MCP-only profile,
   bearer denial, and `404` for every probed non-MCP route.
4. Safety and private-artifact handling — ✅ 371 Python and 3 Node tests plus CI passed; the
   snapshot, rollback program, database roles, MCP credential, and curl configuration are untracked
   owner-only artifacts. This evidence contains no values from them.
5. Failure restores before `AT_BOUND`; deployed code equals HEAD — ✅ the rollback program was tested
   before opening and remains armed; the deployed image digest was published from merged HEAD
   `b5a3f9d11f11476d3b486d50cfe5840efbf32c9c`.

## Running delta table

| Loop | Shipped | Headline |
|---|---|---|
| T0 | Reversible public deployment | Live/ready, 5 MCP tools, 0 exposed application routes |

## ZEN

The result is simple (one service), general (ordinary HTTPS MCP), agent-friendly (tool capability
discovery), structurally fail-closed, and reversible. Provider drift became three small permanent
contract fixes instead of deployment folklore.

## exit → T1

Run the code-derived protocol, tool, lifecycle, abuse, principal-isolation, rotation/revocation,
private aggregate-retrieval, and real Grep sandbox matrix. Do not treat the temporary ingress as a
production network exit.
