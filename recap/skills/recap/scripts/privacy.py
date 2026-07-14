#!/usr/bin/env python3
"""Deterministic defense-in-depth redaction for private Recap artifacts.

Recall owns transcript privacy. This module preserves its markers and protects Recap's additional
git/session metadata without network calls, provider clients, or attempts to recover secret values.
"""

from __future__ import annotations

import re
from typing import Any


PRIVACY_POLICY_VERSION = "recap-defense-redaction-v1"
REDACTION_MARKERS = frozenset({
    "[redacted-secret-line]",
    "[redacted-private-key-block]",
    "[redacted-secret-value]",
    "[REDACTED]",
    "[REDACTED-PRIVATE-KEY]",
})

PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----.*?-----END (?P=label)-----",
    re.DOTALL,
)

PROVIDER_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"(?<![A-Za-z0-9_])sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])sk-(?!ant-|or-v1-)[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])sk-ant-(?:api03|admin01)-[A-Za-z0-9_-]{80,}AA(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])sk-or-v1-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])gsk_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])xai-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])pplx-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])csk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{30,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])xox[baprs]-[A-Za-z0-9-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])ops_[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Z0-9])A[KS]IA[0-9A-Z]{16}(?![A-Z0-9])",
    r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])pcsk_[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])lsv2_[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])",
))
PROXIMITY_SECRET_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bsntryu_[a-f0-9]{64}\b",
    r"sentry(?:.|[\n\r]){0,40}?\b[a-f0-9]{64}\b",
    r"(?:phrase|accessToken|access_token)(?:.|[\n\r]){0,40}?\b[a-z0-9]{64}\b",
))

GENERIC_ASSIGNMENT_RE = re.compile(
    r'''(?ix)(?<![A-Za-z0-9_.-])["']?[A-Za-z0-9_.-]*'''
    r'''(?:api[_-]?key|apikey|api|token|secret|password|passwd|pass|credential|creds|key|'''
    r'''access|private[_-]?key|'''
    r'''access[_-]?key|client[_-]?secret|authorization|auth)[A-Za-z0-9_.-]*["']?'''
    r'''\s*[:=]\s*["']?(?:Bearer\s+)?[^\s,"']{12,}'''
)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.I)
CREDENTIALED_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.I)
SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_.-])(?:litellm[_.-]*master[_.-]*key|api[_.-]*key|apikey|password|passwd|secret|"
    r"authorization|bearer|credential|private[_.-]*key|access[_.-]*key|client[_.-]*secret|"
    r"access[_.-]*token|refresh[_.-]*token|token)(?:$|[_.-](?:value|prefix|suffix|part\d*))",
    re.I,
)
AMBIGUOUS_KEY_RE = re.compile(r"^(?:key|creds|access|api)$", re.I)


def _ambiguous_key_has_secret_value(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[^\s,\"']{12,}", value):
        return False
    safe, redactions = sanitize(value)
    return bool(
        redactions or safe != value
        or (any(char.isalpha() for char in value) and any(char.isdigit() for char in value))
    )


def _has_provider_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in PROVIDER_PATTERNS)


def _has_direct_secret_value(text: str) -> bool:
    return _has_provider_value(text) or any(
        pattern.search(text) for pattern in PROXIMITY_SECRET_PATTERNS
    )


def _has_cross_line_assignment(text: str) -> bool:
    return any(
        "\n" in match.group(0) or "\r" in match.group(0)
        for match in GENERIC_ASSIGNMENT_RE.finditer(text)
    )


def _secret_line(text: str) -> bool:
    return bool(
        _has_direct_secret_value(text)
        or GENERIC_ASSIGNMENT_RE.search(text)
        or BEARER_RE.search(text)
        or CREDENTIALED_URL_RE.search(text)
    )


def sanitize(text: str) -> tuple[str, int]:
    """Redact credential-bearing lines and private-key blocks while preserving safe context."""
    if not isinstance(text, str):
        text = str(text)
    if _has_cross_line_assignment(text):
        return "[redacted-secret-line]", 1
    compact = re.sub(r"\s+", "", text)
    if compact != text and _has_provider_value(compact) and not _has_provider_value(text):
        return "[redacted-secret-line]", 1
    text, private_blocks = PRIVATE_KEY_RE.subn("[redacted-private-key-block]", text)
    redactions = private_blocks
    inserted_private_markers = private_blocks
    safe_lines = []
    for part in text.splitlines(keepends=True):
        line = part.rstrip("\r\n")
        ending = part[len(line):]
        if line in REDACTION_MARKERS or _secret_line(line):
            marker = line if line in REDACTION_MARKERS else "[redacted-secret-line]"
            safe_lines.append(marker + ending)
            if marker == "[redacted-private-key-block]" and inserted_private_markers:
                inserted_private_markers -= 1
            else:
                redactions += 1
        else:
            safe_lines.append(part)
    if not safe_lines and text in REDACTION_MARKERS:
        return text, redactions + 1
    return "".join(safe_lines), redactions


def _fragmented_provider_value(values: list[str]) -> bool:
    candidates = [value for value in values if value and value not in REDACTION_MARKERS]
    return len(candidates) > 1 and _has_provider_value("".join(candidates))


def _leaf_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [] if value in REDACTION_MARKERS else [value]
    if isinstance(value, list):
        return [text for item in value for text in _leaf_strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _leaf_strings(item)]
    return []


def _redact_leaf_strings(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return ("[redacted-secret-value]", 1) if value and value not in REDACTION_MARKERS else (value, 0)
    if isinstance(value, list):
        result, count = [], 0
        for item in value:
            safe, redactions = _redact_leaf_strings(item)
            result.append(safe)
            count += redactions
        return result, count
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            safe, redactions = _redact_leaf_strings(item)
            result[key] = safe
            count += redactions
        return result, count
    return value, 0


def sanitize_structure(value: Any) -> tuple[Any, int]:
    """Scrub nested evidence, including key-labeled and cross-field credential fragments."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, list):
        result = []
        count = 0
        for item in value:
            safe, redactions = sanitize_structure(item)
            result.append(safe)
            count += redactions
        leaves = _leaf_strings(result)
        if _fragmented_provider_value(leaves):
            result, redacted = _redact_leaf_strings(result)
            count += redacted
        return result, count
    if isinstance(value, dict):
        result = {}
        count = 0
        for key, item in value.items():
            key_text = str(key)
            if (
                SENSITIVE_KEY_RE.search(key_text)
                or (AMBIGUOUS_KEY_RE.fullmatch(key_text) and _ambiguous_key_has_secret_value(item))
            ) and item not in (None, "", [], {}):
                result[key] = "[redacted-secret-value]"
                count += 1
                continue
            safe, redactions = sanitize_structure(item)
            result[key] = safe
            count += redactions
        leaves = _leaf_strings(result)
        if _fragmented_provider_value(leaves):
            result, redacted = _redact_leaf_strings(result)
            count += redacted
        return result, count
    return value, 0
