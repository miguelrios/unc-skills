from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from itertools import pairwise
import json
import os
import re
import stat
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

from .transport import open_no_redirect


POLICY_VERSION = "recall-privacy-v1"
REDACTION = "[REDACTED:{category}]"
ALLOWED_JUDGE_CATEGORIES = {
    "contextual_name",
    "contextual_address",
    "contextual_financial",
    "contextual_government",
    "contextual_medical",
}
SENSITIVE_KEY = re.compile(
    r"(?:litellm.*master.*key|api[_-]?key|password|secret|authorization|bearer|access[_-]?token|refresh[_-]?token|private[_-]?key)$",
    re.I,
)
APPROVED_JUDGE_BASE_URL_ENV = "RECALL_PRIVACY_JUDGE_ALLOWED_BASE_URL"


def _canonical_https_base_url(value: str, *, label: str) -> tuple[str, str, int, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port or 443
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an HTTPS URL without credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    return "https", parsed.hostname.casefold().rstrip("."), port, path


@dataclass(frozen=True)
class PrivacyDecision:
    action: str
    value: Any
    categories: dict[str, int]
    reason_code: str
    policy_version: str = POLICY_VERSION

    def receipt(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "categories": dict(sorted(self.categories.items())),
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
        }


def summarize_receipts(receipts: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    actions: dict[str, int] = {}
    categories: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for receipt in receipts:
        action = receipt["action"]
        actions[action] = actions.get(action, 0) + 1
        reason = receipt["reason_code"]
        reasons[reason] = reasons.get(reason, 0) + 1
        for category, count in receipt["categories"].items():
            categories[category] = categories.get(category, 0) + int(count)
    return {
        "mode": mode,
        "policy_version": POLICY_VERSION,
        "actions": dict(sorted(actions.items())),
        "categories": dict(sorted(categories.items())),
        "reason_codes": dict(sorted(reasons.items())),
    }


def _luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _structural_spans(text: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []

    def add(pattern: str, category: str, *, flags: int = 0, group: int = 0,
            predicate: Callable[[str], bool] | None = None) -> None:
        for match in re.finditer(pattern, text, flags):
            start, end = match.span(group)
            value = match.group(group)
            if predicate is None or predicate(value):
                spans.append({"start": start, "end": end, "category": category})

    add(r"\b(?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)\s*[=:]\s*[^\s,;]+", "credential", flags=re.I)
    add(r"\bBearer\s+[^\s,;]+", "credential", flags=re.I)
    add(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", "email", flags=re.I, group=1)
    add(r"(?<![\w.])((?:\+1\s+|\(\d{3}\)\s*)\d{3}[- ]\d{4}|\+1\s+\d{3}[- ]\d{3}[- ]\d{4})(?![\w.-])", "phone", group=1)
    add(r"(?<![\w.])(\d{3}-\d{3}-\d{4})(?![\w.-])", "phone", group=1)
    add(r"(?<!\d)(\d{3}-\d{2}-\d{4})(?!\d)", "government_id", group=1)
    add(r"(?<![\d.])((?:\d[ -]?){12,18}\d)(?![\d.-])", "financial_id", group=1, predicate=_luhn)
    add(r"\b(?:MRN|medical record(?: number)?)\s*:\s*([A-Z0-9-]{6,})", "medical_id", flags=re.I, group=1)
    add(r"\bAddress\s*:\s*([^\n]+)", "postal_address", flags=re.I, group=1)
    return spans


def _validated_spans(spans: list[dict[str, Any]], text: str, *, agentic: bool) -> list[dict[str, Any]]:
    clean = []
    for span in spans:
        if set(span) != {"start", "end", "category"}:
            raise ValueError("privacy span has invalid fields")
        start, end, category = span["start"], span["end"], span["category"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise ValueError("privacy span is out of bounds")
        if not isinstance(category, str) or not category:
            raise ValueError("privacy span category is invalid")
        if agentic and category not in ALLOWED_JUDGE_CATEGORIES:
            raise ValueError("privacy judge returned an unsupported category")
        clean.append({"start": start, "end": end, "category": category})
    clean.sort(key=lambda item: (item["start"], item["end"]))
    for left, right in pairwise(clean):
        if right["start"] < left["end"]:
            raise ValueError("privacy spans overlap")
    return clean


class AgenticJudge:
    """Ephemeral, schema-validated contextual-PII adapter for staging LiteLLM."""

    def __init__(self, *, base_url: str, virtual_key: str, model: str, timeout: float = 20.0):
        approved_base_url = os.environ.get(APPROVED_JUDGE_BASE_URL_ENV)
        if not approved_base_url:
            raise ValueError(
                f"privacy judge requires an approved staging LiteLLM base URL in {APPROVED_JUDGE_BASE_URL_ENV}"
            )
        approved = _canonical_https_base_url(
            approved_base_url, label="approved staging LiteLLM base URL",
        )
        configured = _canonical_https_base_url(base_url, label="privacy judge base URL")
        if configured != approved:
            raise ValueError("privacy judge must use the exact approved staging LiteLLM base URL")
        if not virtual_key or not model:
            raise ValueError("privacy judge requires a scoped virtual key and model")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.virtual_key = virtual_key
        self.model = model
        self.timeout = timeout

    def __call__(self, text: str) -> list[dict[str, Any]]:
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (
                    "Return JSON only as {spans:[{start,end,category}]}. Mark contextual personal "
                    "names, addresses, financial, government, or medical identifiers. Offsets are "
                    "zero-based Unicode character offsets. Do not repeat or explain input text."
                )},
                {"role": "user", "content": text},
            ],
        }, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Authorization": "Bearer " + self.virtual_key, "Content-Type": "application/json"},
        )
        with open_no_redirect(request, timeout=self.timeout) as response:
            outer = json.loads(response.read())
        content = outer["choices"][0]["message"]["content"]
        payload = json.loads(content)
        if set(payload) != {"spans"} or not isinstance(payload["spans"], list):
            raise ValueError("privacy judge response has invalid schema")
        return _validated_spans(payload["spans"], text, agentic=True)


def load_scoped_virtual_key(path: Path) -> str:
    key_path = Path(path).expanduser()
    try:
        metadata = key_path.lstat()
    except OSError:
        raise PermissionError("privacy judge virtual-key file is unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PermissionError("privacy judge virtual-key file must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PermissionError("privacy judge virtual-key file must be private")
    if metadata.st_size > 65_536:
        raise PermissionError("privacy judge virtual-key file exceeds maximum byte count")
    try:
        descriptor = os.open(key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) & 0o077
                or opened.st_size > 65_536
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise PermissionError("privacy judge virtual-key file changed during validation")
            raw = os.read(descriptor, 65_537).decode("utf-8", errors="strict").strip()
        finally:
            os.close(descriptor)
    except PermissionError:
        raise
    except (OSError, UnicodeDecodeError):
        raise PermissionError("privacy judge virtual-key file could not be read safely") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("privacy judge key file must be scoped-key JSON") from error
    if not isinstance(parsed, dict) or set(parsed) != {"virtual_key", "scope", "expires_at"}:
        raise ValueError("privacy judge key file must contain virtual_key, scope, and expires_at")
    if parsed["scope"] != "recall-privacy-judge":
        raise ValueError("privacy judge virtual key has the wrong scope")
    try:
        expires = datetime.fromisoformat(str(parsed["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError
        now = datetime.now(timezone.utc)
        expires = expires.astimezone(timezone.utc)
        if not now < expires <= now + timedelta(hours=24):
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError("privacy judge virtual key must expire within 24 hours") from error
    value = parsed.get("virtual_key")
    if not isinstance(value, str) or not value:
        raise ValueError("privacy judge virtual-key file is empty")
    return value


class PrivacyPolicy:
    def __init__(self, *, mode: str = "off", judge: Callable[[str], list[dict[str, Any]]] | None = None,
                 judge_failure: str = "drop"):
        if mode not in {"off", "scrub", "drop"}:
            raise ValueError("privacy mode must be off, scrub, or drop")
        if judge_failure not in {"drop", "ignore"}:
            raise ValueError("judge failure must be drop or ignore")
        self.mode = mode
        self.judge = judge
        self.judge_failure = judge_failure

    def apply(self, value: Any) -> PrivacyDecision:
        if self.mode == "off":
            return PrivacyDecision("keep", value, {}, "policy_off")
        categories: dict[str, int] = {}
        judge_failed = False

        def count(category: str) -> None:
            categories[category] = categories.get(category, 0) + 1

        def visit(item: Any) -> Any:
            nonlocal judge_failed
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if SENSITIVE_KEY.search(str(key)) and child is not None and child != "" and child != [] and child != {}:
                        count("credential")
                        result[key] = REDACTION.format(category="credential")
                    else:
                        result[key] = visit(child)
                return result
            if isinstance(item, list):
                return [visit(child) for child in item]
            if not isinstance(item, str):
                return item
            spans = _structural_spans(item)
            if self.judge is not None:
                try:
                    spans.extend(_validated_spans(self.judge(item), item, agentic=True))
                except Exception:
                    if self.judge_failure == "drop":
                        judge_failed = True
            if not spans:
                return item
            spans.sort(key=lambda span: (span["start"], -(span["end"] - span["start"])))
            merged = []
            for span in spans:
                if merged and span["start"] < merged[-1]["end"]:
                    continue
                merged.append(span)
            for span in merged:
                count(span["category"])
            rendered = item
            for span in reversed(merged):
                replacement = REDACTION.format(category=span["category"])
                rendered = rendered[:span["start"]] + replacement + rendered[span["end"]:]
            return rendered

        scrubbed = visit(copy.deepcopy(value))
        if judge_failed:
            return PrivacyDecision("drop", None, categories, "judge_unavailable")
        if not categories:
            return PrivacyDecision("keep", value, {}, "no_match")
        if self.mode == "drop":
            return PrivacyDecision("drop", None, categories, "sensitive_match")
        return PrivacyDecision("scrub", scrubbed, categories, "sensitive_match")
