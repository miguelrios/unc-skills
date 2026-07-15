# Recap synthesis contract

The host agent authors semantic meaning from bounded redacted packets. Deterministic code validates
references, coverage, ordering, git facts, test outcomes, and length. No script calls a model.

## Top-level shape

```json
{
  "schema_version": "recap.synthesis-draft.v1",
  "manifest_sha256": "<collect receipt hash>",
  "accounting_sha256": "<accounting receipt hash>",
  "headline": {"...": "one evidence-bound item"},
  "story": [],
  "timeline": [],
  "changes": [],
  "verification": [],
  "failures_recoveries": [],
  "final_state": [],
  "open_work": [],
  "coverage": {"low_signal_group_ids": []}
}
```

Every top-level field is required. `story` and `timeline` must be non-empty. Story references every
accounting claim exactly once and carries all event IDs owned by each grouped claim. Timeline covers
every significant claim event exactly once in strictly ordered, non-overlapping entries; it may
split a thematic claim across multiple times. Their event partitions must differ. Coverage lists
every sealed low-signal group exactly once.

## Common item fields

```json
{
  "id": "unique-stable-id",
  "title": "Required outside headline",
  "summary": "A concise factual statement",
  "source_label": "session_observed",
  "accounting_claim_ids": ["claim-id"],
  "evidence_ids": ["event-id"]
}
```

Summaries are limited to 1,200 characters; titles to 160. Unknown fields fail validation.

Source labels mean:

- `session_observed`: user/tool/session evidence directly supports the statement.
- `agent_report`: the evidence proves the assistant reported the statement, not that it was true;
  every referenced event must be an assistant event.
- `verified_now`: a current read-only git snapshot supports the statement. Include `git_evidence`;
  event/accounting lists may be empty.
- `inference`: the statement is a bounded interpretation of listed evidence. Include a non-empty
  `caveat` describing the uncertainty.

`git_evidence` entries have exactly `repo_root`, `kind`, and `value`. Kinds are `head`, `branch`,
`changed_path`, `clean`, and `available`. The validator requires exact equality with the manifest's
verified-now snapshot. Current git evidence cannot be attached to a historical source label.

## Section-only fields

- Story: `narrative_role` is `setup`, `approach`, `turning_point`, `outcome`, or `remaining`.
- Timeline: `first_ordinal` and `last_ordinal` exactly bound all events in the entry's claims;
  entries sort by the first ordinal.
- Changes: `paths` and `commits` are required string lists. Session-observed values must exist in
  the event-linked git index; verified-now values must exist in the current snapshot.
- Verification: `outcome`, `command_event_id`, and `result_event_id`. An observed command must be
  in the manifest test index and its outcome/result must match exactly. Without a command, only
  `discussed_only` or `unverifiable_now` is allowed.
- Failures/recoveries, final state, and open work add no fields beyond the common item shape.

## Complete minimal example

```json
{
  "schema_version": "recap.synthesis-draft.v1",
  "manifest_sha256": "abc",
  "accounting_sha256": "def",
  "headline": {
    "id": "headline",
    "summary": "The requested change was implemented and checked.",
    "source_label": "session_observed",
    "accounting_claim_ids": ["goal", "change"],
    "evidence_ids": ["event-1", "event-8"]
  },
  "story": [{
    "id": "story-outcome",
    "title": "From request to result",
    "summary": "The request led to one supported change.",
    "source_label": "session_observed",
    "accounting_claim_ids": ["goal", "change"],
    "evidence_ids": ["event-1", "event-8"],
    "narrative_role": "outcome"
  }],
  "timeline": [{
    "id": "timeline-goal",
    "title": "Goal",
    "summary": "The user set the goal.",
    "source_label": "session_observed",
    "accounting_claim_ids": ["goal"],
    "evidence_ids": ["event-1"],
    "first_ordinal": 1,
    "last_ordinal": 1
  }, {
    "id": "timeline-change",
    "title": "Change",
    "summary": "The session recorded the change.",
    "source_label": "session_observed",
    "accounting_claim_ids": ["change"],
    "evidence_ids": ["event-8"],
    "first_ordinal": 8,
    "last_ordinal": 8
  }],
  "changes": [],
  "verification": [],
  "failures_recoveries": [],
  "final_state": [],
  "open_work": [],
  "coverage": {"low_signal_group_ids": ["routine-reads"]}
}
```

The example hashes and IDs are placeholders; use exact values from the private artifacts. If the
validator rejects the draft twice, stop without an authoritative recap.
