"""Access control and response hardening for the API + dashboard.

This is a single-user tool with no accounts: it binds to 127.0.0.1, and
on this laptop that is the whole security boundary. It stops being one
the moment the app is published, so credentials are **opt-in** - set
DASHBOARD_PASSWORD and every request except /health must carry HTTP
Basic credentials. Unset (the default) leaves behaviour exactly as it
was. Basic auth is what browsers handle natively, so the React dashboard
needs no login screen and its fetch() calls inherit the credentials.

Basic auth sends the password on every request, so it is only meaningful
over a channel nobody else can read: HTTPS, or a private network such as
Tailscale. See docs/deployment notes in README.md.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings, get_settings

# Liveness must stay reachable for container health checks and uptime
# monitoring; it reports only whether Postgres and Redis answer.
_UNPROTECTED_PATHS = frozenset({"/health"})

_SECURITY_HEADERS = {
    # The dashboard renders text taken from third-party career pages; a
    # response must never be re-interpreted as a different content type.
    "X-Content-Type-Options": "nosniff",
    # Nothing here is meant to be embedded, and framing it would only
    # ever be clickjacking.
    "X-Frame-Options": "DENY",
    # Job URLs are third-party; don't leak what the dashboard was showing.
    "Referrer-Policy": "no-referrer",
}


def _credentials_match(header: str | None, settings: Settings) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    user, _, password = decoded.partition(":")
    # Both halves are compared, in constant time, before they are combined:
    # a short-circuit would let timing distinguish a wrong username from a
    # wrong password.
    user_ok = secrets.compare_digest(user, settings.dashboard_user)
    password_ok = secrets.compare_digest(password, settings.dashboard_password)
    return user_ok and password_ok


class AccessControlMiddleware(BaseHTTPMiddleware):
    """Requires Basic credentials when DASHBOARD_PASSWORD is set, and adds
    the hardening headers to every response either way."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        authentication_required = bool(settings.dashboard_password)
        if (
            authentication_required
            and request.url.path not in _UNPROTECTED_PATHS
            and not _credentials_match(request.headers.get("authorization"), settings)
        ):
            return Response(
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Job Intelligence Bot"',
                    **_SECURITY_HEADERS,
                },
            )
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response
