# Hands Free

**Your phone is the terminal.** Step away from the keyboard and let the agent keep cooking —
when it needs you, **Unc calls**: *"Yo, it's Unc. Your agent's cooking and wants your blessing:
deploy snapshot v6 to prod. Say approve or deny — or hit 1 to bless it, 2 to shut it down."*
Works with **Claude Code**, **OpenAI Codex**, and **pi**, powered by Vapi.

[![skills.sh](https://skills.sh/b/miguelrios/unc-skills)](https://skills.sh/miguelrios/unc-skills/hands-free)

## No hooks. The agent decides.

hands-free is a **skill plus one script** — nothing else:

- The **skill** tells the agent how to behave while you're away: never end a turn on an
  unanswered chat question; when you need the user, call them; before anything hard to
  reverse, call for a blessing; one question per call; redial once max.
- The **script** (`call_user.py`) is the only moving part: `ask "<question>"` prints your
  spoken answer, `approve "<summary>"` prints `approve` or `deny`. Voicemail is detected
  and reported as *no answer* — never mistaken for you.

No hook watches your prompts, no hook intercepts your tools, nothing dials on its own.
The agent reads the skill, judges when it genuinely needs you, and places the call —
the same way it decides to run any other tool.

> Versions ≤ 0.2.x wired harness hooks (including one that could dial your phone on every
> tool call). Re-running `install` removes all of that wiring.

## Requirements

- Claude Code, Codex, or pi
- Node.js 18+ and Python 3.9+
- A Vapi private API key, phone number id, and a destination number in E.164 format (e.g. `+15555550123`)

## Install

Native collection installs:

```bash
# Claude Code
claude plugin marketplace add miguelrios/unc-skills
claude plugin install hands-free@unc-skills

# Codex
codex plugin marketplace add miguelrios/unc-skills
codex plugin add hands-free@unc-skills

# pi (installs all unc-skills)
pi install git:github.com/miguelrios/unc-skills
```

For pi package installs, put credentials in `~/.config/hands-free/.env`; the bundled skill
script reads that portable location directly.

The standalone npm installer supports these commands starting with `@parcha/hands-free@0.3.0`.
Until that version is published, use one of the native collection installs above.

```bash
npx @parcha/hands-free@0.3.0 install      # auto-detects a harness
# or pick one explicitly:
npx @parcha/hands-free@0.3.0 install --harness=claude-code
npx @parcha/hands-free@0.3.0 install --harness=codex
npx @parcha/hands-free@0.3.0 install --harness=pi
```

Then fill in the env file the installer dropped:

```bash
# Claude Code:
$EDITOR ~/.claude/hands-free/.env
# Codex:
$EDITOR ~/.codex/hands-free/.env
# pi:
$EDITOR ~/.pi/agent/hands-free/.env
# portable alternative for every harness:
$EDITOR ~/.config/hands-free/.env
```

Verify with:

```bash
npx @parcha/hands-free@0.3.0 doctor
```

Use it: tell your agent **`activate hands free`** and walk away. Say
**`deactivate hands free`** when you're back. The mode lives in the conversation —
there's no state file to get stuck.

## How a call works

`call_user.py` creates a transient Vapi assistant per call — your agent's question is the
opening line (spoken by **Unc**; override the greeting with `HANDS_FREE_GREETING`), your
speech comes back as a transcript, and the script prints the user-attributed side. Exit
codes carry the contract: `0` answer, `2` config problem, `3` no usable answer (voicemail,
silence, ambiguity) — so the agent can never mistake a dead call for a decision.

## File Layout After Install

```
<harness home>/                # ~/.claude, ~/.codex, or ~/.pi/agent
├── hands-free/
│   ├── .env                   # your Vapi credentials (chmod 600)
│   └── scripts/call_user.py   # the one moving part
└── skills/hands-free/
    ├── SKILL.md               # the agent's playbook
    ├── references/setup.md
    ├── agents/openai.yaml
    └── scripts/call_user.py   # bundled copy
```

## Notes

- API credentials live only in `<harness home>/hands-free/.env` (mode 600).
- The default voice is Vapi `Elliot`; override `VAPI_VOICE_ID` in the env file.
- Prompt text and spoken replies are sent to Vapi. Review your Vapi retention settings before using with sensitive code.
- The installer only writes its own files; the single exception is *removing* legacy hands-free hook entries from `settings.json` / `hooks.json`.

## Manual Install (without npx)

Copy `scripts/call_user.py` and `skills/hands-free/` into your harness home as laid out
above, and put credentials in `<harness home>/hands-free/.env`. That's the whole install.

## Publishing

```bash
npm test
npm publish --access public
```
