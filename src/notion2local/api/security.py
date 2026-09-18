from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status


def require_setup_token(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.setup_token_value
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SETUP_TOKEN is not configured on the server",
        )
    provided = request.headers.get("X-Setup-Token")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid setup token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
