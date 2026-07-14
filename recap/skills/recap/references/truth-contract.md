# Recap truth contract

## Observable truth

Recap may claim only what is supported by visible session events, Recall metadata, observed command
results, and repository snapshots. It never reports hidden reasoning. A visible assistant
explanation is evidence that the assistant said something, not proof the statement is true.

## Boundary

The default unit is one native session file or canonical Recall session receipt. The session ID,
harness, source digest, and observed byte range form the boundary receipt. Child agents,
continuations, and resumed runs remain separate unless explicitly included. Nested evidence keeps
its own session label and ordinal space.

For a live session, collection records source size before and after parsing. A changed size means the
manifest is partial even if every byte in the initial snapshot was parsed.

## Evidence accounting

Normalize each observed event as `(session_id, ordinal, event_id, timestamp, surface, text_sha256)`.
An output claim lists one or more event IDs. Every event must be covered once by a claim or by one
low-signal group. Duplicate coverage and missing ordinals are validation failures.

Structural manifest completeness is not semantic recap completeness. The former means every parsed
event has a contiguous ordinal and valid digest; the latter requires the claim/group assignment
above. Never report semantic zero-unaccounted before that assignment exists.

The exhaustive event ledger, episode index, packet index, and repeat-group index are immutable
JSONL streams with independent byte counts and SHA-256 receipts. Validation replays them in bounded
memory and rejects missing, duplicate, reordered, or tampered evidence. Semantic packets are bounded
at 1,000 events or 128 KiB of redacted text. Cache keys cover exact complete packet content; only
the unfinished tail changes when a live session appends.

Semantic accounting is a separate `recap.accounting.v1` overlay sealed to the event-ledger hash.
Claims own explicit event IDs; low-signal groups own non-overlapping ordinal ranges plus a count and
event-ID digest. Validation streams the ledger and requires every event to have exactly one owner.
This separation lets the host agent exercise judgment without letting prose redefine structural
truth.

Low-signal grouping is semantic, not deletion. Typical candidates are repeated polls with unchanged
state, duplicate status output, or mechanical progress notices. A group records count and ordinal
ranges. User decisions, edits, commands with side effects, errors, tests, and final results are never
silently treated as noise.

## Git attribution

Keep three facts distinct:

1. **Observed action:** an event shows an edit or git command was attempted.
2. **Observed result:** its tool result shows success, failure, or unknown status.
3. **Current state:** a git snapshot shows the repository now contains a commit, diff, or path.

Only join them into a causal claim when evidence connects them. Dirty state that predates the
session, edits by another worktree, amended/rebased commits, and reverted changes require explicit
qualification. Missing repositories or expired history are coverage limits, not empty results.

The machine schema keeps these facts in three non-overlapping objects:

- `session_observed`: exact event IDs for attempted mutations, git commands, branch switches,
  reported commits, tests/checks, and their direct or order-inferred results;
- `session_end`: historical state, defaulting to explicitly unknown; and
- `verified_now`: current read-only repository snapshots labeled `verified_now_only`.

Repository candidates come from Recall cwd metadata, structured tool workdirs, event-linked file
mutations, and explicit repeated `--repo` arguments. Git probes are fixed argv—not transcript
commands—run without a shell, optional locks, terminal prompts, filesystem monitors, or unbounded
time/output. A current dirty path is never promoted into `session_observed` merely because it exists.

## Verification

Classify tests and checks as `passed`, `failed`, `retried`, `discussed_only`, or
`unverifiable_now`. A passing command can support only the scope it actually ran. Current re-runs
are new observations and must not be presented as historical session evidence.

## Privacy

Private manifests use mode 0600 and directories use 0700. Public receipts contain only hashes,
counts, durations, versions, and completeness flags. Paths, prompts, transcript text, diffs,
credentials, and external message contents stay private unless the user deliberately publishes
them.
