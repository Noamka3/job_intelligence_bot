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

Two things Basic auth does not give you, added here: browsers attach
cached Basic credentials to requests from *other* sites too, so a page
elsewhere could submit a form to /candidate/resume with those credentials
(CSRF) - state-changing requests that the browser marks as cross-site
are refused; and nothing slows a password guesser down - an address
that keeps failing is locked out for a while.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings, get_settings

# Liveness must stay reachable for container health checks and uptime
# monitoring; it reports only whether Postgres and Redis answer.
_UNPROTECTED_PATHS = frozenset({"/health"})

# Browsers send Sec-Fetch-Site on every request; "cross-site" means the
# request was made by a page on another site. Reads are harmless (the
# response is not readable cross-origin without CORS); writes are not.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Wrong passwords per client address before it is locked out, and for
# how long: generous for a person, hopeless for a guesser.
_MAX_FAILED_AUTH = 20
_FAILED_AUTH_WINDOW_SECONDS = 15 * 60

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


class FailedAttempts:
    """Recent wrong passwords per client address, in memory: the API is
    one process, and a restart forgiving everyone is fine."""

    def __init__(self) -> None:
        self._by_client: dict[str, deque[float]] = defaultdict(deque)

    def record(self, client: str) -> None:
        self._by_client[client].append(time.monotonic())

    def locked_out(self, client: str) -> bool:
        attempts = self._by_client[client]
        cutoff = time.monotonic() - _FAILED_AUTH_WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= _MAX_FAILED_AUTH


def _is_cross_site_write(request: Request) -> bool:
    return (
        request.method not in _SAFE_METHODS
        and request.headers.get("sec-fetch-site", "").lower() == "cross-site"
    )


class AccessControlMiddleware(BaseHTTPMiddleware):
    """Requires Basic credentials when DASHBOARD_PASSWORD is set, refuses
    cross-site writes, and adds the hardening headers to every response."""

    failed_attempts = FailedAttempts()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _is_cross_site_write(request):
            return Response(status_code=403, headers=_SECURITY_HEADERS)
        settings = get_settings()
        if settings.dashboard_password and request.url.path not in _UNPROTECTED_PATHS:
            client = request.client.host if request.client else ""
            if self.failed_attempts.locked_out(client):
                return Response(status_code=429, headers=_SECURITY_HEADERS)
            if not _credentials_match(request.headers.get("authorization"), settings):
                self.failed_attempts.record(client)
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
