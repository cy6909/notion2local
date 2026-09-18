from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, status

ADMIN_SESSION_COOKIE = "n2l_admin_session"


def setup_token_is_valid(request: Request, provided: str | None) -> bool:
    expected = request.app.state.settings.setup_token_value
    return bool(expected and provided and secrets.compare_digest(provided, expected))


def admin_session_value(setup_token: str) -> str:
    return hmac.new(
        setup_token.encode("utf-8"),
        b"notion2local-admin-session-v1",
        hashlib.sha256,
    ).hexdigest()


def admin_session_is_valid(request: Request) -> bool:
    expected = request.app.state.settings.setup_token_value
    supplied = request.cookies.get(ADMIN_SESSION_COOKIE)
    return bool(expected and supplied and secrets.compare_digest(supplied, admin_session_value(expected)))


def require_setup_token(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.setup_token_value
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SETUP_TOKEN is not configured on the server",
        )
    if admin_session_is_valid(request):
        return

    provided = request.headers.get("X-Setup-Token")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
    if not setup_token_is_valid(request, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid setup token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
