"""API-key authentication. Applied to every route except /health, /ready, /metrics, webhooks."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from agent.config import Settings

API_KEY_HEADER = "X-API-Key"
_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def require_api_key(
    settings: Settings = Depends(_settings),  # noqa: B008 - FastAPI dependency
    provided: str | None = Depends(_header),  # noqa: B008
) -> None:
    if not settings.auth_enabled:
        return  # development only; production config requires API_KEYS
    if provided and any(hmac.compare_digest(provided, k) for k in settings.api_keys):
        return
    raise HTTPException(status_code=401, detail="invalid or missing API key")
