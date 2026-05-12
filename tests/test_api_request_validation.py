"""Unit tests for the custom RequestValidationError handler.

The handler lives in server_manager.create_fastapi_app and returns a
structured 422 body when an MCP client passes `q` as a nested object
instead of a plain string.

Integration-test note
---------------------
/passages/search is a GET endpoint whose `q` parameter arrives via the
query string.  TestClient has no mechanism to send a nested JSON object
as a query-string value, so the FastAPI validation path that the handler
guards cannot be exercised end-to-end through the ASGI test client.
We therefore test the handler function directly with a synthetic
RequestValidationError.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_exc(errors: list[dict]) -> RequestValidationError:
    """Build a RequestValidationError from a list of raw error dicts.

    Pydantic v2 requires real InitErrorDetails for ValidationError
    construction, so we build the exception the way FastAPI itself does:
    by passing the list directly to RequestValidationError's internal
    _errors store via the errors= kwarg accepted by the class.
    """
    # RequestValidationError stores errors as-is when passed a list of dicts
    # via the body kwarg pathway.  The simplest portable approach is to
    # subclass and inject, but FastAPI actually accepts a list of dicts in
    # the constructor when the first positional arg (errors) is provided.
    # We use the private _errors attribute injection as a last resort.
    exc = RequestValidationError.__new__(RequestValidationError)
    exc._errors = errors  # type: ignore[attr-defined]
    return exc


def _get_handler():
    """Return the query_must_be_string_handler from a freshly built app."""
    manager = UnifiedServerManager()
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    app = manager.create_fastapi_app(config)
    # FastAPI stores exception handlers in exception_handlers dict
    handler = app.exception_handlers.get(RequestValidationError)
    assert handler is not None, "RequestValidationError handler not registered"
    return handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nested_q_returns_structured_422() -> None:
    """Handler returns code=query_must_be_string when q has string_type error."""
    handler = _get_handler()
    exc = _make_exc(
        [
            {
                "loc": ("query", "q"),
                "msg": "Input should be a valid string",
                "type": "string_type",
                "input": {"text": "BRCA1"},
                "url": "https://errors.pydantic.dev/2/v/string_type",
            }
        ]
    )
    request = MagicMock()
    response: JSONResponse = await handler(request, exc)

    assert response.status_code == 422
    import json

    body = json.loads(response.body)
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "query_must_be_string"
    assert "recovery_hint" in detail
    assert "next_commands" in detail
    assert detail["next_commands"][0]["tool"] == "search_passages"


@pytest.mark.asyncio
async def test_nested_q_dict_type_returns_structured_422() -> None:
    """Handler also fires for dict_type errors on q."""
    handler = _get_handler()
    exc = _make_exc(
        [
            {
                "loc": ("query", "q"),
                "msg": "Input should be a valid string",
                "type": "dict_type",
                "input": {"text": "BRCA1"},
            }
        ]
    )
    request = MagicMock()
    response: JSONResponse = await handler(request, exc)

    assert response.status_code == 422
    import json

    body = json.loads(response.body)
    assert body["detail"]["code"] == "query_must_be_string"


@pytest.mark.asyncio
async def test_unrelated_validation_error_falls_through() -> None:
    """Handler falls through to default shape for errors not involving q."""
    handler = _get_handler()
    exc = _make_exc(
        [
            {
                "loc": ("query", "limit"),
                "msg": "Input should be less than or equal to 50",
                "type": "less_than_equal",
                "input": 999,
            }
        ]
    )
    request = MagicMock()
    response: JSONResponse = await handler(request, exc)

    assert response.status_code == 422
    import json

    body = json.loads(response.body)
    # Fall-through returns exc.errors() under "detail" key — a list
    detail = body["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == ["query", "limit"]


@pytest.mark.asyncio
async def test_multiple_errors_q_present_returns_structured_422() -> None:
    """When multiple errors exist and one is for q, return the structured form."""
    handler = _get_handler()
    exc = _make_exc(
        [
            {
                "loc": ("query", "limit"),
                "msg": "Input should be less than or equal to 50",
                "type": "less_than_equal",
                "input": 999,
            },
            {
                "loc": ("query", "q"),
                "msg": "Input should be a valid string",
                "type": "string_type",
                "input": {"text": "BRCA1"},
            },
        ]
    )
    request = MagicMock()
    response: JSONResponse = await handler(request, exc)

    assert response.status_code == 422
    import json

    body = json.loads(response.body)
    assert body["detail"]["code"] == "query_must_be_string"
