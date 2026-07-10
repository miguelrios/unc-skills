#!/usr/bin/env python3
"""parable_usage — read live subscription headroom across the pools, pre-turn.

The load-balancing story rests on one fact: every subscription parable routes to
publishes its own remaining-headroom over an authenticated HTTP probe that costs
ZERO model tokens and needs NO turn. The brain reads these before it routes, so
"which pool has room" is measured, not guessed-from-throttle-after-the-fact.

Three probes, each reading the same credential the local harness already stored
(this module never mints or writes a token):

  claude  (Anthropic Max/Pro)  GET  api.anthropic.com/api/oauth/usage
          -> ~/.claude/.credentials.json  .claudeAiOauth.accessToken  (OAuth, user:profile scope)
          windows: five_hour, seven_day, seven_day_opus  (utilization 0-100, resets_at ISO)

  codex   (ChatGPT Pro/Plus)   GET  chatgpt.com/backend-api/wham/usage
          -> ~/.codex/auth.json  .tokens.access_token + .tokens.account_id
          windows: primary (5h), secondary (weekly)  (used_percent 0-100, reset_at unix)

  cursor  (Cursor Pro/Ultra)   POST api2.cursor.sh/auth/exchange_user_api_key  (key->JWT)
          then POST .../aiserver.v1.DashboardService/GetCurrentPeriodUsage
          -> $CURSOR_API_KEY  (env)   included-budget cents: limit / remaining

All three endpoints are internal/undocumented (the same ones the official CLIs
call); shapes can shift between CLI versions. Every probe fails SOFT — a missing
credential, a 401 on a stale token, or a shape change yields status="unknown"
with a reason, never an exception. Unknown headroom means "route as if it has
room" — the probe informs, it never blocks.

stdlib only (urllib), so parable.py stays dependency-free.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HTTP_TIMEOUT = 6.0  # seconds; a probe is a pre-flight, never a bottleneck

# The usage endpoints throttle rapid polling — Claude's /api/oauth/usage in particular
# trips into a multi-minute HTTP 429 cooldown after a burst. A short on-disk cache means
# repeated `parable usage` calls within a window reuse the last read instead of re-hitting
# the endpoint, so the brain can poll freely without ever tripping the limit. Headroom
# does not move meaningfully second-to-second, so a stale-by-seconds read is fine.
CACHE_TTL_SECONDS = 45
_CACHE_PATH = Path(tempfile.gettempdir()) / f"parable-usage-cache-{os.getuid() if hasattr(os, 'getuid') else 'u'}.json"


def _get_json(url: str, headers: dict, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def _mins_until(unix_or_iso) -> int | None:
    """Minutes from now until a reset time given as unix seconds or ISO-8601."""
    try:
        if isinstance(unix_or_iso, (int, float)):
            when = datetime.fromtimestamp(unix_or_iso, tz=timezone.utc)
        else:
            when = datetime.fromisoformat(str(unix_or_iso).replace("Z", "+00:00"))
        return max(0, round((when - datetime.now(timezone.utc)).total_seconds() / 60))
    except Exception:
        return None


def _unknown(pool: str, reason: str) -> dict:
    return {"pool": pool, "status": "unknown", "reason": reason, "windows": []}


# ---------------------------------------------------------------------------
# claude — Anthropic subscription (OAuth)
# ---------------------------------------------------------------------------

def probe_claude() -> dict:
    cred = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / ".credentials.json"
    if not cred.is_file():
        return _unknown("claude", f"no {cred}")
    try:
        oauth = json.loads(cred.read_text()).get("claudeAiOauth", {})
        token = oauth.get("accessToken")
    except Exception as e:
        return _unknown("claude", f"unreadable credentials ({e})")
    if not token:
        return _unknown("claude", "no accessToken in credentials")
    try:
        body = _get_json(
            "https://api.anthropic.com/api/oauth/usage",
            {"Authorization": f"Bearer {token}",
             "anthropic-beta": "oauth-2025-04-20",
             "Content-Type": "application/json",
             "User-Agent": "parable-usage/1"})
    except urllib.error.HTTPError as e:
        return _unknown("claude", f"HTTP {e.code}"
                        + (" (token stale — run any claude cmd to refresh)" if e.code == 401 else ""))
    except Exception as e:
        return _unknown("claude", f"probe failed ({e})")

    return {"pool": "claude", "status": "ok", "plan": oauth.get("subscriptionType"),
            "windows": claude_windows(body)}


def claude_windows(body: dict) -> list[dict]:
    """Normalize the /api/oauth/usage body into window dicts. Prefers the newer
    limits[] array: it carries weekly_scoped (per-model) buckets the flat
    five_hour/seven_day fields omit — and the scoped weekly cap on the brain's own
    model is often the TIGHTEST window, so missing it under-counts real budget
    pressure. Falls back to the flat fields when limits[] is absent."""
    windows = []
    limits = body.get("limits")
    if isinstance(limits, list) and limits:
        label_for = {"session": "5h", "weekly_all": "7d"}
        for lim in limits:
            if not isinstance(lim, dict) or lim.get("percent") is None:
                continue
            kind = lim.get("kind")
            if kind == "weekly_scoped":
                model = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
                label = f"7d-{model.lower()}"
            else:
                label = label_for.get(kind, kind or "?")
            windows.append({"window": label,
                            "used_pct": round(float(lim["percent"]), 1),
                            "resets_in_min": _mins_until(lim.get("resets_at")),
                            "severity": lim.get("severity")})
    if not windows:  # fall back to the flat fields if limits[] is absent/empty
        for key, label in (("five_hour", "5h"), ("seven_day", "7d"), ("seven_day_opus", "7d-opus")):
            w = body.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                windows.append({"window": label,
                                "used_pct": round(float(w["utilization"]), 1),
                                "resets_in_min": _mins_until(w.get("resets_at"))})
    return windows


# ---------------------------------------------------------------------------
# codex — ChatGPT subscription (backend token)
# ---------------------------------------------------------------------------

def probe_codex() -> dict:
    auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    if not auth.is_file():
        return _unknown("codex", f"no {auth}")
    try:
        tokens = json.loads(auth.read_text()).get("tokens", {})
        token, acct = tokens.get("access_token"), tokens.get("account_id", "")
    except Exception as e:
        return _unknown("codex", f"unreadable auth.json ({e})")
    if not token:
        return _unknown("codex", "no access_token in auth.json (API-key mode has no plan usage)")
    try:
        body = _get_json(
            "https://chatgpt.com/backend-api/wham/usage",
            {"Authorization": f"Bearer {token}",
             "ChatGPT-Account-Id": acct or "",
             "User-Agent": "parable-usage/1"})
    except urllib.error.HTTPError as e:
        return _unknown("codex", f"HTTP {e.code}"
                        + (" (token stale — run any codex cmd to refresh)" if e.code == 401 else ""))
    except Exception as e:
        return _unknown("codex", f"probe failed ({e})")

    rl = body.get("rate_limit", {})
    windows = []
    for key, label in (("primary_window", "5h"), ("secondary_window", "7d")):
        w = rl.get(key)
        if isinstance(w, dict) and w.get("used_percent") is not None:
            windows.append({"window": label,
                            "used_pct": round(float(w["used_percent"]), 1),
                            "resets_in_min": _mins_until(w.get("reset_at"))})
    return {"pool": "codex", "status": "ok", "plan": body.get("plan_type"),
            "windows": windows}


# ---------------------------------------------------------------------------
# cursor — Cursor subscription (API key -> JWT exchange -> dashboard RPC)
# ---------------------------------------------------------------------------

def probe_cursor(env_key: str = "CURSOR_API_KEY") -> dict:
    key = os.environ.get(env_key)
    if not key:
        return _unknown("cursor", f"${env_key} not set")
    base = os.environ.get("CURSOR_API_BASE_URL", "https://api2.cursor.sh")
    try:
        exchanged = _get_json(f"{base}/auth/exchange_user_api_key",
                              {"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                              data=b"{}")
        access = exchanged.get("accessToken")
        if not access:
            return _unknown("cursor", "key exchange returned no accessToken")
        body = _get_json(f"{base}/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
                         {"Authorization": f"Bearer {access}",
                          "Content-Type": "application/json"},
                         data=b"{}")
    except urllib.error.HTTPError as e:
        return _unknown("cursor", f"HTTP {e.code}")
    except Exception as e:
        return _unknown("cursor", f"probe failed ({e})")

    pu = body.get("planUsage", {})
    limit, remaining = pu.get("limit"), pu.get("remaining")
    if limit in (None, 0):
        return _unknown("cursor", "no included-budget limit in response")
    used_pct = round(100.0 * (1 - (remaining or 0) / limit), 1)
    # Cursor bills an included dollar budget, not a rolling %-window: model it as a
    # single "cycle" window so the brain reads one uniform shape across pools.
    return {"pool": "cursor", "status": "ok", "plan": "cursor",
            "windows": [{"window": "cycle", "used_pct": used_pct,
                         "resets_in_min": _mins_until(body.get("billingCycleEnd")),
                         "remaining_usd": round((remaining or 0) / 100, 2),
                         "limit_usd": round(limit / 100, 2)}]}


PROBES = {"claude": probe_claude, "codex": probe_codex, "cursor": probe_cursor}


def _probe_one(name: str, cursor_env_key: str) -> dict:
    if name == "cursor":
        return probe_cursor(cursor_env_key)
    if name in PROBES:
        return PROBES[name]()
    return _unknown(name, "no such pool")


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _write_cache(cache: dict) -> None:
    try:  # best-effort; a probe must never fail because its cache is unwritable
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(_CACHE_PATH)
        os.chmod(_CACHE_PATH, 0o600)
    except Exception:
        pass


def probe_all(pools: list[str] | None = None, cursor_env_key: str = "CURSOR_API_KEY",
              ttl: int = CACHE_TTL_SECONDS, use_cache: bool = True) -> list[dict]:
    """Probe each pool, backed by a per-pool short-TTL disk cache. A fresh cached
    entry is returned without hitting the network; on a live-probe failure the last
    good cached read is served (marked cached+stale) rather than reverting to
    unknown — so a throttled endpoint keeps informing routing. Set use_cache=False
    (or ttl=0) to force a live read of every pool."""
    now = time.time()
    cache = _read_cache() if use_cache else {}
    out, dirty = [], False
    for name in (pools or list(PROBES)):
        entry = cache.get(name) if use_cache else None
        if entry and entry.get("_ok") and (now - entry.get("_ts", 0)) < ttl:
            out.append({k: v for k, v in entry.items() if not k.startswith("_")} | {"cached": True})
            continue
        fresh = _probe_one(name, cursor_env_key)
        if fresh.get("status") == "ok":
            cache[name] = {**fresh, "_ts": now, "_ok": True}
            dirty = True
            out.append(fresh)
        elif entry and entry.get("_ok"):
            # live probe failed (e.g. HTTP 429) but we have a prior good read — serve it
            # stale rather than dropping the pool to unknown mid-batch.
            age = round(now - entry.get("_ts", 0))
            out.append({k: v for k, v in entry.items() if not k.startswith("_")}
                       | {"cached": True, "stale_seconds": age, "live_probe": fresh.get("reason")})
        else:
            out.append(fresh)
    if dirty:
        _write_cache(cache)
    return out


def worst_used_pct(pool_report: dict) -> float | None:
    """The tightest window drives routing — a pool is only as free as its most-used window."""
    pcts = [w["used_pct"] for w in pool_report.get("windows", []) if w.get("used_pct") is not None]
    return max(pcts) if pcts else None


def format_report(reports: list[dict]) -> str:
    lines = []
    for r in reports:
        if r["status"] != "ok":
            lines.append(f"  {r['pool']:7} unknown — {r.get('reason', '')}")
            continue
        worst = worst_used_pct(r)
        head = f"  {r['pool']:7} {r.get('plan') or '?':8}"
        wins = "  ".join(
            f"{w['window']}={w['used_pct']:.0f}%"
            + (f"(${w['remaining_usd']:.2f} left)" if "remaining_usd" in w else "")
            + (f"↻{w['resets_in_min']}m" if w.get("resets_in_min") is not None else "")
            for w in r["windows"])
        flag = " ⚠ TIGHT" if worst is not None and worst >= 80 else ""
        if r.get("stale_seconds") is not None:
            flag += f" (cached {r['stale_seconds']}s — live probe: {r.get('live_probe')})"
        elif r.get("cached"):
            flag += " (cached)"
        lines.append(f"{head} {wins}{flag}")
    return "\n".join(lines) if lines else "  (no pools probed)"


if __name__ == "__main__":
    import sys
    as_json = "--json" in sys.argv
    reports = probe_all()
    if as_json:
        print(json.dumps(reports, indent=1))
    else:
        print("parable usage — live subscription headroom (zero model tokens)")
        print(format_report(reports))
