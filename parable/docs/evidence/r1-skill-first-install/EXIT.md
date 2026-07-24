# R1 EXIT — Skill-first magical install

Status: complete

## Acceptance map

1. **One installed-skill entrypoint.** `skills/parable/parable.sh` resolves only
   files beneath its installed skill directory. It stages the pinned runtime
   under `~/.local/share/parable/<version>`, creates
   `~/.local/bin/parable`, and enters the existing setup/native-auth flow. This
   works from a test copy containing only the skill; no source checkout or
   executable-code download is used.
2. **Idempotent and non-destructive.** Clean-home integration coverage pins the
   install, rerun, shell-PATH marker, private `0700`/`0600` setup modes, and
   modified generated-config refusal. Separate cases prove an unrelated
   `parable` command is preserved, missing Node fails before install, and a
   existing user `.bashrc` content survives unchanged ahead of Parable's appended block.
3. **Honest fresh-terminal handoff.** Authorized synthetic setup prints exactly
   `In a new terminal, open your project and run:` followed by
   `parable claude --brain auto -- --effort high`. Setup failures and
   `--no-auth` staging never print that ready phrase.
4. **One installation story.** `SKILL.md` recognizes install/onboard intent and
   runs its sibling script. README, the subscription guide, provider reference,
   package/plugin metadata, and source `install.sh` all converge on the same
   bootstrap. This matches Claude Code's cached-plugin portability boundary.
5. **Automatic and explicit parents.** The launcher accepts `auto`, `fable`,
   `sol`, and `config`. Pure policy tests pin Fable preference below 80%, Sol
   fallback when Claude is tight, unknown-usage behavior, both-tight behavior,
   explicit selection, and unconfigured-model refusal. The all-vendor
   integration launch selects exact Fable without reading real credentials.
6. **Verification and delivery.** `cd parable && npm test` passed all 115 tests.
   `npm pack --dry-run --json` produced `@parcha/parable@0.1.10` with 37 files,
   including `parable.sh`, runtime JS, VERSION, Python engine, and checksum-
   pinned proxy patch. `git diff --check` passed. Delivery is
   [PR #213](https://github.com/miguelrios/unc-skills/pull/213); npm publication
   remains an explicit human gate.

## Review and ZEN

- Simple: one skill-owned script, one versioned runtime, one durable command,
  one fresh-terminal launch.
- General: the runtime does not depend on a plugin cache shape or source root;
  version updates can coexist and atomically retarget Parable's own symlink.
- Prompt/agentic: install intent is recognized by the skill, and automatic
  parent selection uses measured headroom without deterministic task matching.
- Beautiful/dope: a user can install a skill in Claude Code, say “Set up
  Parable,” complete native OAuth, and leave with one command that chooses
  Fable or Sol at session start.

## Next loop

R2 audits the merged-main skill-only journey and package contents, then reports
the release boundary and any genuine remaining losing cells.
