# Hands-free setup & troubleshooting

## Install

```bash
npx @parcha/hands-free install                    # auto-detects Claude Code vs Codex
npx @parcha/hands-free install --harness=codex    # or force one
```

The installer copies `call_user.py` and this skill into the harness home — **it installs no
hooks and touches no hook settings except to REMOVE wiring left by hands-free ≤ 0.2.x**
(which hooked UserPromptSubmit/PreToolUse/Stop and could dial on every tool call). It seeds
`<harness home>/hands-free/.env` from the template on first install.

## Credentials (`<harness home>/hands-free/.env`)

| Var | What |
|---|---|
| `VAPI_API_KEY` | Vapi API key |
| `VAPI_PHONE_NUMBER_ID` | The Vapi number that places calls |
| `HANDS_FREE_PHONE_NUMBER` | The user's phone, E.164 (e.g. +14155550123) |
| `VAPI_VOICE_ID` | Optional; default `Elliot` |
| `HANDS_FREE_GREETING` | Optional; replaces the default "Yo, it's Unc." opener |

`call_user.py` reads `.env` from its parent hands-free directory, `$HANDS_FREE_HOME/.env`
if set, or plain environment variables. Never ask the user to re-paste values that already
exist; exit 2 names the missing key on stderr.

## Diagnose

```bash
npx @parcha/hands-free doctor
```

Checks the script, the skill, env completeness — and warns if legacy hook wiring is still
present in the harness settings.

## How a call works

`call_user.py` creates a transient Vapi assistant per call — the question is its
`firstMessage`, Vapi speaks it (persona: Unc), the user's speech comes back in the call
artifact, and the script polls until the call ends and prints the user-attributed side.
Voicemail is detected and reported as exit 3, never as an answer. No audio files touch
disk; recording is off, transcript only.
