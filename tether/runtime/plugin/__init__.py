from __future__ import annotations

import asyncio
import functools
import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_runtime() -> ModuleType:
    injected = sys.modules.get("bridge_runtime")
    if injected is not None:
        return injected
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    runtime_path = data_home / "tether" / "bridge_runtime.py"
    spec = importlib.util.spec_from_file_location("tether_bridge_runtime", runtime_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Tether runtime is unavailable at {runtime_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runtime = _load_runtime()
Store = runtime.Store
broker_call = runtime.broker_call
continue_native = runtime.continue_native
deliver_zellij = runtime.deliver_zellij
effective_allowed_users = runtime.effective_allowed_users
load_config = runtime.load_config
start_broker = runtime.start_broker


log = logging.getLogger(__name__)


@dataclass
class PluginState:
    store: Any = field(default_factory=Store)
    broker: Any = None
    bridge_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    active_cancellations: dict[str, threading.Event] = field(default_factory=dict)
    recovery_worker_started: bool = False


state = PluginState()
store = state.store


def _trusted_bot_ids() -> set[str]:
    raw = os.getenv("SLACK_TRUSTED_BOT_IDS", "")
    return {value.strip() for value in raw.replace(",", " ").split() if value.strip()}


def _is_bot_message(event: dict[str, Any]) -> bool:
    return bool(event.get("bot_id")) or event.get("subtype") == "bot_message"


def _trusted_bot_message(event: dict[str, Any]) -> bool:
    if not _is_bot_message(event):
        return True
    identities = {str(event.get("bot_id") or ""), str(event.get("user") or "")}
    identities.discard("")
    return bool(identities.intersection(_trusted_bot_ids()))


def _mark_bridge_thread_before_slack_gate(adapter, event) -> bool:
    thread_ts = str(event.get("thread_ts") or "")
    channel_id = str(event.get("channel") or event.get("channel_id") or "")
    if not thread_ts or not channel_id:
        return False
    team_id = str(
        event.get("team") or event.get("team_id")
        or getattr(adapter, "_channel_team", {}).get(channel_id, "") or ""
    )
    bridge = store.find(team_id, channel_id, thread_ts)
    if bridge is None:
        return False
    adapter._bot_message_ts.add(thread_ts)
    return True


def _install_slack_bridge_prefilter():
    from plugins.platforms.slack.adapter import SlackAdapter
    if not hasattr(SlackAdapter, "_handle_slack_message"):
        raise RuntimeError("Hermes Slack adapter is incompatible with Tether")
    if getattr(SlackAdapter, "_tether_prefilter", False):
        return
    original = SlackAdapter._handle_slack_message

    @functools.wraps(original)
    async def bridged_handle(self, event):
        if not _trusted_bot_message(event):
            log.warning("Tether rejected an untrusted Slack bot message")
            return None
        try:
            _mark_bridge_thread_before_slack_gate(self, event)
        except Exception:
            log.exception("Could not evaluate a Slack bridge thread before the mention gate")
        return await original(self, event)

    SlackAdapter._handle_slack_message = bridged_handle
    SlackAdapter._tether_prefilter = True


def _reply_delta(text: str) -> str:
    marker = "[End of thread context]"
    if marker in text:
        text = text.rsplit(marker, 1)[1]
    return text.strip()


def _suppress_bridge_reaction(event, gateway):
    adapter = gateway.adapters[event.source.platform]
    event_id = str(event.message_id or event.source.message_id or "")
    reacting = getattr(adapter, "_reacting_message_ids", None)
    if reacting is not None:
        reacting.discard(event_id)
    if event_id and hasattr(adapter, "_remove_reaction"):
        asyncio.get_running_loop().create_task(
            adapter._remove_reaction(str(event.source.chat_id), event_id, "eyes")
        )


async def _progress(adapter, bridge):
    delay = 5
    elapsed = 0
    while True:
        await asyncio.sleep(delay)
        elapsed += delay
        text = "_Working in the bound agent session…_" if elapsed < 60 else f"_Still working in the bound agent session ({elapsed // 60}m)…_"
        await adapter.send(bridge.channel_id, text, metadata={"thread_id": bridge.thread_ts})
        delay = 300


async def _stop_progress(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _failure_reason(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential" in text or "authentication" in text or "401" in text:
        return "the native session credential could not be obtained or authenticated"
    if "timed out" in text:
        return "the continuation timed out and was stopped cleanly"
    if "cancelled" in text:
        return "the continuation was cancelled by the operator"
    if "session" in text and ("no longer" in text or "not found" in text or "invalid" in text):
        return "the captured agent session is no longer resumable"
    return "the bound session could not be resumed"


def _run_recovered_event(bridge, item):
    if bridge.source_kind == "zellij_pane":
        deliver_zellij(bridge, item["text"])
        return "_Delivered to the bound Zellij pane._"
    return continue_native(bridge, item["text"])


def _recover_queued_events():
    for bridge_id in store.queued_bridge_ids():
        while True:
            item = store.claim_next_event(bridge_id)
            if item is None:
                break
            bridge = store.get(bridge_id)
            if bridge is None or not bridge.thread_ts:
                store.finish_event(item["event_id"], "bridge is no longer active")
                continue
            try:
                broker_call({
                    "op": "reply", "bridge_id": bridge_id,
                    "text": "_Resuming a queued continuation after a Hermes gateway restart…_",
                })
                response = _run_recovered_event(bridge, item)
                if response:
                    broker_call({"op": "reply", "bridge_id": bridge_id, "text": response})
                store.finish_event(item["event_id"])
            except Exception as exc:
                reason = _failure_reason(exc)
                store.finish_event(item["event_id"], f"{type(exc).__name__}: {reason}")
                log.error("Recovered bridge reply failed for %s: %s", bridge_id, reason)
                try:
                    broker_call({"op": "reply", "bridge_id": bridge_id, "text": f"The recovered continuation failed because {reason}."})
                except Exception:
                    log.error("Could not report recovered bridge failure for %s", bridge_id)


async def _drain_bridge(bridge_id, gateway, platform):
    lock = state.bridge_locks.setdefault(bridge_id, asyncio.Lock())
    async with lock:
        while True:
            item = store.claim_next_event(bridge_id)
            if item is None:
                return
            bridge = store.get(bridge_id)
            if bridge is None or not bridge.thread_ts:
                store.finish_event(item["event_id"], "bridge is no longer active")
                continue
            adapter = gateway.adapters[platform]
            progress = asyncio.create_task(_progress(adapter, bridge))
            cancellation = threading.Event()
            state.active_cancellations[bridge_id] = cancellation
            try:
                if bridge.source_kind == "zellij_pane":
                    await asyncio.to_thread(deliver_zellij, bridge, item["text"])
                    response = "_Delivered to the bound Zellij pane._"
                else:
                    response = await asyncio.to_thread(continue_native, bridge, item["text"], cancellation)
                if response:
                    await adapter.send(bridge.channel_id, response, metadata={"thread_id": bridge.thread_ts})
                store.finish_event(item["event_id"])
            except Exception as exc:
                reason = _failure_reason(exc)
                store.finish_event(item["event_id"], f"{type(exc).__name__}: {reason}")
                log.error("Bridge reply failed for %s: %s", bridge.bridge_id, reason)
                try:
                    await adapter.send(
                        bridge.channel_id,
                        f"I received this reply, but {reason}. Nothing was sent to a different session.",
                        metadata={"thread_id": bridge.thread_ts},
                    )
                except Exception:
                    log.exception("Could not report bridge failure in Slack for %s", bridge.bridge_id)
            finally:
                state.active_cancellations.pop(bridge_id, None)
                await _stop_progress(progress)


def _authorized(bridge, user_id: str) -> bool:
    allowed = set(effective_allowed_users())
    return user_id in allowed and (bridge.owner_user_id == "*" or bridge.owner_user_id == user_id)


def _pre_gateway_dispatch(*, event, gateway, **_kwargs):
    source = event.source
    if getattr(source.platform, "value", "") != "slack" or not source.thread_id:
        return None
    bridge = store.find(str(source.guild_id or ""), str(source.chat_id), str(source.thread_id))
    if bridge is None:
        return None
    user_id = str(source.user_id or "")
    if not _authorized(bridge, user_id):
        return {"action": "skip", "reason": "bridge-user-not-authorized"}
    _suppress_bridge_reaction(event, gateway)
    if bridge.source_kind in {"headless_run", "hermes_session"}:
        run_id = str(bridge.source.get("run_id") or bridge.source.get("session_id") or "unknown")
        cwd = str(bridge.source.get("cwd") or "")
        return {
            "action": "rewrite",
            "text": (
                f"[Durable Hermes continuation for run {run_id}; original working directory {cwd}. "
                "Use the root message and thread history as the run report. The original process may have exited; "
                "continue as an operator conversation and return verified results in this thread.]\n\n" + event.text
            ),
        }
    event_id = str(event.message_id or source.message_id or "")
    delta = _reply_delta(event.text)
    normalized = delta.lower().strip(" .!?")
    if normalized in {"cancel", "stop", "nvm", "never mind", "nevermind"}:
        cancellation = state.active_cancellations.get(bridge.bridge_id)
        cancelled_queued = store.cancel_queued(bridge.bridge_id)
        if cancellation is not None:
            cancellation.set()
            suffix = f" and discarded {cancelled_queued} queued replies" if cancelled_queued else ""
            message = f"_Cancellation requested; stopping the active continuation cleanly{suffix}._"
        elif cancelled_queued:
            message = f"_Cancelled {cancelled_queued} queued replies before execution._"
        else:
            message = "_Nothing is currently running in the bound session._"
        if store.claim_event(event_id, bridge.bridge_id):
            store.finish_event(event_id)
        asyncio.get_running_loop().create_task(
            gateway.adapters[source.platform].send(bridge.channel_id, message, metadata={"thread_id": bridge.thread_ts})
        )
        return {"action": "skip", "reason": "tether-cancel"}
    inserted = store.enqueue_event(event_id, bridge.bridge_id, delta)
    if inserted:
        pending = store.pending_count(bridge.bridge_id)
        if pending > 1:
            asyncio.get_running_loop().create_task(
                gateway.adapters[source.platform].send(
                    bridge.channel_id, f"_Queued behind {pending - 1} active reply._",
                    metadata={"thread_id": bridge.thread_ts},
                )
            )
        asyncio.get_running_loop().create_task(_drain_bridge(bridge.bridge_id, gateway, source.platform))
    return {"action": "skip", "reason": "tether-handled"}


def register(ctx) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        log.error("Tether disabled: Hermes has no Slack bot credential")
        return
    load_config()
    if state.broker is None:
        state.broker = start_broker(token)
    store.requeue_processing()
    if not state.recovery_worker_started and store.queued_bridge_ids():
        threading.Thread(target=_recover_queued_events, name="hermes-bridge-recovery", daemon=True).start()
        state.recovery_worker_started = True
    _install_slack_bridge_prefilter()
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
