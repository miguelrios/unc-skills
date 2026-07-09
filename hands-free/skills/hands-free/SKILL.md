---
name: hands-free
description: Phone-call bridge for coding agents — when the user is away from the keyboard, the agent calls them (as Unc). Use when the user says "activate hands free" / "deactivate hands free" (also hands-free, handsfree), asks to route questions or approvals to a phone call, or says they're stepping away but want the work to keep moving. Works on Claude Code and Codex.
---

# Hands Free

The user is away from the keyboard; **the phone is the terminal**. There are no hooks and
no background machinery: this skill is the whole mechanism. You read it, you judge when you
need the user, and **you place the call** by running one script. Nothing watches your
messages or your tools to guess for you.

## Mode

- `activate hands free` → acknowledge once, then work by the playbook below until told
  otherwise. The mode lives in the conversation, nowhere else.
- `deactivate hands free` → acknowledge, and questions return to chat.

## The playbook (while active)

You own two judgment calls, and both go through the same script:

1. **You need an answer** (a question, a blocker, a choice):
   run `python3 <harness home>/hands-free/scripts/call_user.py ask "<one short,
   phone-friendly question>"` — stdout is the user's reply, verbatim; treat it as if they
   typed it.
2. **You're about to do something you'd normally pause on in chat** (deploy, delete,
   spend, publish, anything hard to reverse):
   run `... call_user.py approve "<one-line summary of the action>"` — stdout is
   `approve` or `deny`. Deny means don't; find the safe alternative or park it.

Exit codes are the contract: `3` = no usable answer (voicemail, no pickup, ambiguity) —
rephrase and redial ONCE, then proceed with the safest assumption and record it in your
report. `2` = configuration problem — consult [references/setup.md](references/setup.md),
fix, retry. **Never redial in a loop; two rings per need is the ceiling.**

**Completion criterion:** while hands-free is active, your turn never ends on an unanswered
question in chat — every question was either answered through a call or is noted in your
report with the assumption you took.

`<harness home>` is `~/.claude` on Claude Code, `~/.codex` on Codex.

## Voice discipline

Speech-to-text is lossy, so the call earns its own etiquette:

- One question per call; batch related decisions into one summary rather than serial calls.
- Ambiguous answer → one short follow-up call, not a guess.
- Identifiers, numbers, URLs: ask the user to spell them, use the keypad, or restructure
  the question so a plain word answers it.

## Setup

If the script or credentials are missing (exit 2, or the file isn't there), follow
[references/setup.md](references/setup.md) — install is `npx @parcha/hands-free install`,
then the user fills `<harness home>/hands-free/.env`.
