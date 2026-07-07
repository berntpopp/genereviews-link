"""Guard: the unauthenticated backend must not enable credentialed CORS.

Security remediation (Theme C, D4). The server holds no cookies or session, so
CORS ``allow_credentials=True`` is meaningless and a footgun — especially if the
allowed origins are ever set to ``*``. We flip credentials OFF, preserve the
existing method list (GET endpoints such as ``/health`` and ``/`` must keep
working), and refuse the unsafe wildcard-origin + credentials combination at
startup. Research use only; not clinical decision support."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager


def _build_app() -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    return UnifiedServerManager().create_fastapi_app(config)


def _cors_kwargs(app: FastAPI) -> dict:
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return dict(mw.kwargs)
    raise AssertionError("CORSMiddleware is not configured on the app")


def test_cors_credentials_disabled() -> None:
    kwargs = _cors_kwargs(_build_app())
    assert kwargs["allow_credentials"] is False


def test_cors_preserves_get_health_endpoint() -> None:
    # Preserving the existing method list means GET endpoints keep working.
    with TestClient(_build_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


def test_startup_guard_rejects_wildcard_with_credentials() -> None:
    from genereview_link.server_manager import validate_cors_config

    with pytest.raises((ValueError, RuntimeError)):
        validate_cors_config(["*"], allow_credentials=True)


def test_startup_guard_allows_wildcard_without_credentials() -> None:
    from genereview_link.server_manager import validate_cors_config

    # Wildcard origins are acceptable as long as credentials are off.
    validate_cors_config(["*"], allow_credentials=False)
