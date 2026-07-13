# Upstream, installation, updates, and local state

## Credit and license boundary

[Desloppify](https://github.com/peteromallet/desloppify) was created by Peter
O'Malley. The official Python engine is licensed under
[OSNL-0.2](https://github.com/peteromallet/desloppify/blob/main/LICENSE). This
MIT-licensed companion skill is independently written, does not vendor the
engine, and is not an official upstream distribution.

Report engine defects and contribute fixes at the upstream repository. Keep
portable-skill packaging issues in `miguelrios/unc-skills`.

## Install the official engine

Desloppify requires Python 3.11 or newer. Prefer an isolated tool environment:

```bash
uv tool install --upgrade 'desloppify[full]>=1,<2'
desloppify --version
```

For an ephemeral run without a persistent installation:

```bash
uvx --from 'desloppify[full]>=1,<2' desloppify --version
```

If `uv` is unavailable, use a dedicated virtual environment. Avoid installing
into a system Python. Verify the package name and upstream project before any
install; never follow a hallucinated package or repository URL.

Do not run `desloppify update-skill` after installing this portable skill. That
command installs upstream's harness-specific document and may replace a
dedicated skill or modify a shared instruction file. Update the two layers
separately:

```text
portable companion: update through the same unc-skills/plugin installer
official engine:    uv tool upgrade 'desloppify<2'
```

Pin an engine version in CI or other reproducible automation. An interactive
operator can choose current releases, but a scan must never auto-upgrade.

## Keep state private

`.desloppify/` contains persistent scan state, blind review packets, runner
prompts/results, and logs derived from the repository. Add it to `.gitignore`
for every scanned project root.

Before committing or packaging, verify:

```bash
git status --short --ignored .desloppify/
git check-ignore .desloppify/
```

Mechanical scans run locally. Subjective reviewers may read code and review
packets through the active harness. Follow repository data-handling policy and
obtain authorization before choosing any external review path.

## Monorepo state

Each `--path` should be one coherent program. Scan independently built sibling
projects separately so language detection, paths, scoring, and persistent state
do not mix unrelated systems. Use the same path for baseline and final scans.
