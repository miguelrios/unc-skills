"""Static web-admin assets and hardened browser session helpers."""

from __future__ import annotations

from http.cookies import SimpleCookie
from importlib.resources import files
import re


ASSETS = {
    "/admin": ("admin.html", "text/html; charset=utf-8"),
    "/admin/": ("admin.html", "text/html; charset=utf-8"),
    "/admin/assets/admin.css": ("admin.css", "text/css; charset=utf-8"),
    "/admin/assets/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
}
SESSION_COOKIE = "recall_admin_session"
CSRF_COOKIE = "recall_admin_csrf"
COOKIE_VALUE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


def asset(path: str) -> tuple[bytes, str] | None:
    configured = ASSETS.get(path)
    if configured is None:
        return None
    name, content_type = configured
    payload = files("recall_server").joinpath("static", name).read_bytes()
    return payload, content_type


def cookies(raw: str | None) -> dict[str, str]:
    if not raw or len(raw) > 4096:
        return {}
    parsed = SimpleCookie()
    try:
        parsed.load(raw)
    except Exception:
        return {}
    values = {}
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        value = parsed.get(name)
        if value is not None and COOKIE_VALUE.fullmatch(value.value):
            values[name] = value.value
    return values


def session_headers(session: str, csrf: str, max_age: int = 43200) -> list[str]:
    return [
        (
            f"{SESSION_COOKIE}={session}; Max-Age={max_age}; Path=/admin; "
            "Secure; HttpOnly; SameSite=Strict"
        ),
        (
            f"{CSRF_COOKIE}={csrf}; Max-Age={max_age}; Path=/admin; "
            "Secure; SameSite=Strict"
        ),
    ]


def clear_session_headers() -> list[str]:
    return [
        f"{SESSION_COOKIE}=; Max-Age=0; Path=/admin; Secure; HttpOnly; SameSite=Strict",
        f"{CSRF_COOKIE}=; Max-Age=0; Path=/admin; Secure; SameSite=Strict",
    ]


__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "asset",
    "clear_session_headers",
    "cookies",
    "session_headers",
]
