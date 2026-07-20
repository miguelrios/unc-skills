# O2 — VENDOR AUTH AND PROXY LIFECYCLE · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

Parable now delegates ChatGPT, Claude, and xAI authorization directly to the
configured CLIProxyAPI executable with the pinned native flags and inherited
terminal. Credential-safe status reports only provider presence/counts and
mode/parse aggregates. `parable proxy start` keeps CLIProxyAPI in the
foreground with inherited stdio, forwarded signals, and its exit status.

## What shipped

| Piece | Where |
|---|---|
| Auth/status/foreground implementation | `lib/onboarding.js` |
| `auth` and `proxy start` CLI dispatch | `bin/parable.js` |
| Hermetic lifecycle and redaction proof | `tests/test_integration.py` |
| Sanitized receipt | `docs/evidence/o2-auth-proxy-lifecycle/receipt.json` |

`parable setup` now runs native auth for the selected vendors in canonical
order unless the operator passes `--no-auth`. That opt-out enables headless
ChatGPT device auth through the separate `parable auth add chatgpt --device`
path.

## Bound accounting (honest)

- Correctly wired PROVE runs: 1/2 maximum; passed 5/5.
- Evidence failures: 0.
- Instrument failures: 1. The first local source-location grep was scoped too
  broadly beneath the home directory and was truncated. The corrected probe
  was limited to the installed CLIProxyAPI tree; neither probe changed state.
- Review rounds: 1/3. Native argv, selection gates, status field access,
  symlink/mode refusal, stdout inheritance, signal forwarding, child status,
  callback guidance, and setup auto-auth were checked.
- Provider turns: 0. OAuth flows: 0. Provider-record mutations: 0.

ZEN check: one declarative vendor→native-flag map and one aggregate status
scanner cover all vendors. Parable accesses only each safe JSON record's
top-level `type`; it never names a record or touches a credential field. No
provider adapter, callback parser, daemon, broker, or OAuth implementation was
added.

## Accept criteria → evidence

1. Exact native mapping — ✅ fake-binary capture pins Codex browser/device,
   Claude no-browser, and xAI no-browser argv including the generated config.
2. Additive auth and redacted status — ✅ pre-existing sentinel records remain
   byte-identical across auth delegation; JSON/text status expose no sentinel,
   filename, path, account field, or unrecognized provider label. Unsafe-mode
   records and symlinks are counted but never opened.
3. Fail before subprocess — ✅ missing setup, missing binary, unsupported Kimi,
   unselected xAI, and invalid Claude `--device` all leave the fake invocation
   ledger empty.
4. Foreground lifecycle — ✅ fake proxy receives only exact `--config ...
   --local-model`, writes directly to inherited stdout, and its deliberate exit
   17 is preserved. SIGINT, SIGTERM, and SIGHUP handlers forward while active.
5. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (O0→On)

| Loop | Shipped | Headline |
|---|---|---|
| O0 | Contract + baseline | No setup/auth/proxy surface; 81/81 baseline |
| O1 | Secure setup + pinned builder | 9/9 focused; 91/91 complete |
| O2 | Native auth + safe status + foreground proxy | 5/5 focused; 96/96 complete |

## Merge verification

- JSON invariants and privacy boundary: PASS.
- Complete Parable suite: PASS, 96/96.
- npm pack dry-run: PASS, 33 files.
- Local Claudio-Michel review: 5/5.
- Focused PR: pending.
- Verified merged `main`: pending.

## exit → O3

Reconcile exact selected ids against the loopback model catalog, generate only
the entitled named agents, and prove the complete first launch hermetically.
