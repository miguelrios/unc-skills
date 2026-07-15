"""Small authenticated-HTTP guardrails shared by Recall clients."""

from __future__ import annotations

import ssl
import urllib.request
from typing import Any


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTPError before credentials can move hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_no_redirect(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open one HTTP(S) request with normal TLS verification and no redirects."""

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirect(),
    )
    return opener.open(request, timeout=timeout)
