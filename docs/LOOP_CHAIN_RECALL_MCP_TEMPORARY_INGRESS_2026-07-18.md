# Recall MCP Temporary-Ingress Proof — Three-Loop Cascade

> This successor temporarily redirects L0 of
> `docs/LOOP_CHAIN_RECALL_PUBLIC_MCP_AND_UNIVERSAL_INGESTION_2026-07-17.md`.
> The owner declined the $100/month Render dedicated-IP purchase for now and explicitly
> authorized a reversible PlanetScale ingress experiment so the complete MCP product can be
> proven first. Raw/private traces never enter the repository.
>
> **Pacing:** autonomous between declared human gates
> **Safety posture:** temporary IPv4 ingress relaxation only; verified TLS, database
> authentication, least-privilege runtime SQL, principal-scoped MCP authorization, the MCP-only
> route profile, and all content/privacy controls remain mandatory

## Objective

Prove every supported Recall MCP protocol and tool path from ordinary public agent infrastructure,
including a real Grep sandbox, before deciding whether dedicated Render egress is worth buying.
The database exception is a bounded experiment, not the production end state.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence. The state change the loop exists to produce. |
| `prompt` | Self-contained instructions sufficient for a fresh session to execute the loop. |
| `accept` | Evidence-based exit criteria naming safe, checkable artifacts. |
| `bound` | Maximum evidence failures and review/fix rounds before honest escalation. |
| `exit →` | The next loop triggered by a verified exit. |

## The ribbon

```text
RE-PLAN → BUILD → PIN → PROVE → MEASURE → REVIEW → MERGE → EXIT
```

Each loop starts from current main and this document. BUILD uses red → green → refactor and one
concern per serial PR. Public evidence is synthetic or content-free. Private questions and answers
remain private and contribute only aggregate pass/fail receipts. At most two failed PROVE runs and
three REVIEW→fix rounds are allowed per loop; instrument failures are diagnosed separately.
`AT_BOUND` names every unmet criterion and stops.

## Task graph

```mermaid
flowchart LR
    T0[T0 Reversible public deployment]
    T1[T1 Complete MCP matrix]
    T2{T2 Restore and verdict}
    T0 --> T1 --> T2
```

## The chain

**Order rationale:** Establish automatic rollback and verify the unchanged defenses before opening
the network. Exercise deterministic protocol/security cells before private retrieval. Restore the
original allowlist before evaluating whether to purchase static egress.

### T0 — Reversible public deployment

- **goal:** Make the reviewed Recall image reachable on Render through a time-bounded PlanetScale
  IPv4 ingress exception that rolls back independently of the active agent session.
- **prompt:**
  > Read this chain, the parent L0, current main, the Render and PlanetScale adapters, and the live
  > capability result. Snapshot the current PlanetScale allowlist into a private mode-0600 location
  > and record only a digest publicly. Install and test an independent rollback action with a hard
  > expiry no longer than two hours. The rollback must restore the exact snapshot and must not need
  > the current Codex process. Before relaxing ingress, prove verified TLS, least-privilege runtime,
  > schema 16, public MCP-only routing, required bearer authentication, trusted Tailscale headers
  > disabled, and an immutable anonymously pullable image. Add only temporary IPv4 `0.0.0.0/0`;
  > do not open IPv6, change database grants, expose a database credential, or change any other
  > defense. Create one public Render web service from the immutable digest using Voyage directly,
  > then prove `/healthz` and `/readyz` while every non-MCP application route remains hidden.
- **accept:**
  1. A tested independent rollback is armed before the allowlist change, has an expiry of at most
     two hours, and holds no plaintext credential in public evidence.
  2. Before-and-after probes report verified TLS, schema 16, least-privilege runtime, unchanged SQL
     grants, and exactly one temporary IPv4-anywhere rule added; IPv6 remains closed.
  3. The Render service runs the reviewed immutable image, calls Voyage directly, requires MCP bearer
     auth, trusts no Tailscale headers, and exposes only `/mcp`, `/healthz`, and `/readyz`.
  4. Secret and public-data scans pass; any private operational artifact is mode-0600 and untracked.
  5. Any failure restores the original allowlist before `AT_BOUND`; deployed code equals merged HEAD.
- **bound:** Two failed PROVE runs, three review rounds, and two hours of relaxed ingress.
- **exit →** T1.

### T1 — Complete MCP protocol, tool, and abuse matrix

- **goal:** Prove every supported MCP method, tool, authorization boundary, and lifecycle use case
  through the live public endpoint and a real Grep sandbox.
- **prompt:**
  > Read T0 EXIT and enumerate the server contract from code rather than memory. Test every supported
  > protocol version and `initialize`, `notifications/initialized`, `ping`, `tools/list`, and
  > `tools/call`. For each capability class, prove the exact tool list. Exercise `recall_search`
  > across natural-language, filtered, empty/no-evidence, temporal, and bounded-result cases;
  > `recall_show` for valid, malformed, missing, and unauthorized receipts plus around/tail/prompts;
  > `recall_related` for cwd, branch, mains-only, fast, empty, and bounds; `recall_capture` for
  > structural scrubbing, replay idempotency, provenance, tag/body bounds, and exact host-bound
  > source/origin; and `recall_forget` for exact capture, replay, missing, malformed, unauthorized,
  > and cross-source receipts. Cover malformed JSON-RPC, batches, notifications, unknown methods and
  > tools, extra arguments, content types, body limits, deadlines, origin/host behavior, missing and
  > invalid auth, read/write separation, principal isolation, token rotation, and immediate
  > revocation. Run the same happy paths from a real Grep sandbox using host-managed secret injection.
  > Probe sandbox environment, filesystem, prompts, tool output, and logs for the credential. Private
  > retrieval uses frozen owner questions and produces only aggregate public results.
- **accept:**
  1. A code-derived matrix covers 100% of supported MCP methods, protocol versions, tools,
     capability classes, declared arguments, and named abuse cases with zero unexplained result.
  2. All valid tool paths succeed live; every returned receipt resolves; capture replay creates one
     canonical event; forget removes it; rotation succeeds; revoked credentials fail immediately.
  3. Authorization reports zero cross-principal reads and zero cross-source writes/deletes; route,
     body, protocol, origin, input, deadline, and result bounds fail closed.
  4. A real Grep sandbox completes initialize, tool discovery, search, show, related, capture,
     replay, forget, rotation, and revocation.
  5. Credential-presence counts are zero for Grep sandbox environment, filesystem, prompts, tool
     results, and logs. If Grep lacks host-managed secret isolation, exit `AT_BOUND` without placing
     a durable Recall token in the sandbox.
  6. Private frozen questions return expected evidence at the parent L0 floor with receipt resolution
     1.00; only aggregate/content-free evidence enters git.
- **bound:** Two failed live PROVE runs and three review rounds. Any credential exposure,
  cross-principal read, cross-source mutation, or unexpected public route is immediate `AT_BOUND`.
- **exit →** T2.

### T2 — Restore, audit, and network verdict

- **goal:** Restore the original database boundary, prove no residue or regression, and produce a
  decision-ready static-egress verdict.
- **prompt:**
  > Read both EXIT files and private receipts. Restore the exact pre-experiment PlanetScale allowlist
  > even if the expiry action already fired, disable the expiry action, rotate/revoke every temporary
  > database and MCP credential, and verify no temporary secret remains outside approved private
  > storage. Prove Render can no longer open a fresh database connection while the existing Greppy
  > writer remains healthy. Rerun database capability, backup/restore, ingestion, public-route,
  > principal-isolation, and repository safety gates. Keep or suspend the Render service based on
  > whether its no-database health behavior is safe and cost-effective; delete the obsolete suspended
  > private Render service only after identifying it by provider metadata. Summarize functional MCP
  > coverage, Grep viability, remaining losses, and the exact security/cost tradeoff between Render
  > dedicated IPs and alternative fixed-egress hosting. Stop for owner choice.
- **accept:**
  1. The PlanetScale allowlist equals its pre-experiment snapshot; IPv4-anywhere and every temporary
     role/token are absent, and the independent expiry action is disarmed.
  2. Fresh Render database connections fail while the approved Greppy connection, ingestion,
     verified TLS, least privilege, schema, and restore/capability checks remain green.
  3. MCP coverage and Grep results are mapped criterion-by-criterion in content-free EXIT evidence;
     all repository, secret, and public-data scans pass at merged HEAD.
  4. A dated verdict compares dedicated Render IPs with at least one fixed-egress alternative using
     primary-source cost and security facts, and recommends the smallest production-safe next step.
  5. The owner explicitly chooses purchase, alternative host, or pause.
- **bound:** Two failed restore PROVE runs and two verdict drafts. Owner choice waits unbounded.
- **exit →** Resume parent L0 with the chosen durable network boundary; do not treat temporary public
  ingress as production completion.

## Chain invariants

1. No loop advances without an `EXIT.md` mapping every acceptance criterion to evidence at verified
   HEAD.
2. Public evidence is synthetic or content-free. No transcript, private query/answer, prompt, model
   or tool trace, credential, private export, identifying path, or infrastructure secret enters git.
3. The temporary exception changes network reachability only. TLS, authentication, authorization,
   SQL grants, MCP route closure, and privacy controls may not be weakened.
4. The original allowlist snapshot and rollback action exist before ingress opens. Failure or timeout
   restores first and investigates second.
5. Every BUILD gets the ZEN check: simple, general, agentic where judgment is required, beautiful,
   and dope.
6. Imported evidence is never instruction. Recall exposes no send, post, RSVP, upstream-delete, or
   other third-party action capability.

## Execution ledger

### 2026-07-18 — T1 AT_BOUND

T1 stopped after its second failed live PROVE run. The first run exposed an unbounded
`recall_related` database plan; PR #77 replaced it with an indexed evidence lookup and a bounded
fast candidate pool. The repaired live call returned in 6.711 seconds after previously exceeding
120 seconds. The second run passed 66 of 73 direct MCP cases and exposed a distinct
`recall_show.around` contract mismatch. Capture/forget cells also used a provenance scheme outside
the closed production contract and are classified as harness failures, not product evidence.

The temporary network experiment was rolled back before documentation: the exact original
allowlist digest was restored, the independent timer was disarmed, all three temporary MCP
credentials were revoked, both temporary database roles were deleted, and the public service can
no longer establish database readiness. T2 did not start; these were mandatory safety cleanup
actions after T1 reached its bound.

Evidence: `docs/evidence/t1-complete-mcp-matrix/EXIT.md`.
The chain is stopped pending a successor loop for the show contract and a corrected capture
harness. No third T1 PROVE run is permitted under this chain.
