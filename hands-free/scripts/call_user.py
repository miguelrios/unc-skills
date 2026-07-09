#!/usr/bin/env python3
"""call_user — hands-free's only moving part: the agent places a phone call, Unc speaks it.

No hooks, no daemons, no state. The AGENT reads the hands-free skill, decides it needs
the user, and runs:

  call_user.py ask "Which auth provider should I wire up?"
      -> stdout: the caller's answer, verbatim
  call_user.py approve "Deploy snapshot v6 to prod"
      -> stdout: approve | deny

Exit codes (the agent's contract — no silent fake-success):
  0  usable answer on stdout
  2  configuration problem (missing Vapi env values); details on stderr
  3  the call produced no usable answer (voicemail, no pickup, silence, ambiguity);
     details on stderr — rephrase and redial once, then proceed with the safest
     assumption and say so

Credentials: <this script's parent dir>/../.env, or $HANDS_FREE_HOME/.env, or plain
environment variables (VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, HANDS_FREE_PHONE_NUMBER).
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.vapi.ai"


def env_home():
    explicit = os.environ.get("HANDS_FREE_HOME")
    if explicit:
        return pathlib.Path(explicit).expanduser()
    return pathlib.Path(__file__).resolve().parent.parent


def load_env():
    env = {}
    env_path = env_home() / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({key: value for key, value in os.environ.items() if key.startswith("VAPI_") or key.startswith("HANDS_FREE_")})
    return env


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def vapi_request(method, path, api_key, payload=None, timeout=30):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hands-free/0.3",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vapi HTTP {error.code}: {detail[:500]}") from error


def build_assistant(message, purpose):
    env = load_env()
    greeting = env.get("HANDS_FREE_GREETING", "Yo, it's Unc.")
    persona = (
        "You are Unc — the user's laid-back, sharp-eared uncle who bridges phone calls "
        "for their coding agent. Warm, brief, zero filler; you sound like family, and you "
        "handle business in one breath. "
    )
    if purpose == "approval":
        first_message = (
            f"{greeting} Your agent's cooking and wants your blessing: "
            f"{message} "
            "Say approve or deny — or hit 1 to bless it, 2 to shut it down."
        )
        system_message = persona + (
            "Capture exactly one approval decision. "
            "If the user says approve, yes, one, or presses 1, acknowledge warmly and say exactly: ending call now. "
            "If the user says deny, no, two, or presses 2, acknowledge without pushback and say exactly: ending call now. "
            "Do not discuss the coding task itself."
        )
        max_duration = 75
    else:
        first_message = f"{greeting} Quick one from your agent: {message}"
        system_message = persona + (
            "Capture the user's answer to the agent's question. "
            "Do not answer the question yourself. Do not ask unrelated follow-up questions. "
            "When the user gives an answer, acknowledge briefly and say exactly: ending call now."
        )
        max_duration = 150

    return {
        "name": "Unc",
        "firstMessage": first_message[:1800],
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "temperature": 0,
            "maxTokens": 120,
            "messages": [{"role": "system", "content": system_message}],
        },
        "voice": {
            "provider": "vapi",
            "voiceId": env.get("VAPI_VOICE_ID", "Elliot"),
            "speed": 1,
        },
        "backgroundSound": "off",
        "maxDurationSeconds": max_duration,
        "endCallPhrases": ["ending call now"],
        "voicemailDetection": {
            "provider": "openai",
            "beepMaxAwaitSeconds": 10,
        },
        "voicemailMessage": "",
        "artifactPlan": {
            "recordingEnabled": False,
            "loggingEnabled": False,
            "transcriptPlan": {
                "enabled": True,
                "assistantName": "Unc",
                "userName": "User",
            },
        },
        "keypadInputPlan": {
            "enabled": True,
            "timeoutSeconds": 2,
            "delimiters": ["#"],
        },
    }


def place_call(message, purpose):
    env = load_env()
    api_key = env.get("VAPI_API_KEY")
    phone_number_id = env.get("VAPI_PHONE_NUMBER_ID")
    user_number = env.get("HANDS_FREE_PHONE_NUMBER")
    if not api_key or not phone_number_id or not user_number:
        raise RuntimeError("Missing VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, or HANDS_FREE_PHONE_NUMBER")

    payload = {
        "name": f"Hands free {purpose}",
        "phoneNumberId": phone_number_id,
        "customer": {"number": user_number, "name": "Hands free user"},
        "assistant": build_assistant(message, purpose),
    }
    created = vapi_request("POST", "/call", api_key, payload, timeout=30)
    call_id = created.get("id")
    if not call_id:
        raise RuntimeError("Vapi did not return a call id")

    deadline = time.monotonic() + 190
    call = created
    while time.monotonic() < deadline:
        time.sleep(3)
        call = vapi_request("GET", f"/call/{call_id}", api_key, timeout=20)
        if call.get("status") == "ended" or call.get("endedAt"):
            return call
    raise RuntimeError(f"Timed out waiting for Vapi call {call_id}")


def extract_user_answer(call, allow_unattributed=False):
    artifact = call.get("artifact") or {}
    messages = artifact.get("messages") or call.get("messages") or []
    user_parts = []
    for item in messages:
        role = normalize(str(item.get("role") or item.get("speaker") or item.get("type")))
        content = item.get("message") or item.get("content") or item.get("transcript") or item.get("text")
        if content and any(token in role for token in ("user", "customer", "caller")):
            user_parts.append(str(content).strip())
    if user_parts:
        return " ".join(user_parts).strip()

    transcript = artifact.get("transcript") or call.get("transcript") or ""
    user_lines = []
    for line in transcript.splitlines():
        if re.match(r"^\s*(user|customer|caller)\s*:", line, re.I):
            user_lines.append(re.sub(r"^\s*(user|customer|caller)\s*:\s*", "", line, flags=re.I).strip())
    if user_lines:
        return " ".join(user_lines).strip()
    if allow_unattributed:
        return transcript.strip()
    return ""


def approval_decision(answer):
    text = normalize(answer)
    if re.search(r"\b(deny|denied|decline|reject|rejected|no|two|2)\b", text):
        return "deny"
    if re.search(r"\b(approve|approved|allow|allowed|yes|yep|yeah|one|1)\b", text):
        return "allow"
    return None


def main(argv):
    if len(argv) < 3 or argv[1] not in ("ask", "approve"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode = argv[1]
    message = " ".join(argv[2:]).strip()
    if not message:
        print("call_user: empty message", file=sys.stderr)
        return 2
    purpose = "approval" if mode == "approve" else "input"
    try:
        call = place_call(message, purpose)
    except RuntimeError as error:
        print(f"call_user: {error}", file=sys.stderr)
        return 2 if "Missing" in str(error) else 3
    ended_reason = str(call.get("endedReason") or "")
    if "voicemail" in ended_reason.lower():
        print(f"call_user: the call hit voicemail ({ended_reason}) — no live answer", file=sys.stderr)
        return 3
    if purpose == "approval":
        answer = extract_user_answer(call, allow_unattributed=False)
        decision = approval_decision(answer)
        if decision is None:
            print(f"call_user: ambiguous decision from the call: {answer!r}", file=sys.stderr)
            return 3
        print("approve" if decision == "allow" else "deny")
        return 0
    answer = extract_user_answer(call, allow_unattributed=True)
    if not answer:
        print("call_user: the call captured no answer", file=sys.stderr)
        return 3
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
