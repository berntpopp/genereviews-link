"""Structured error payloads for MCP-recoverable failures."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class FieldError(BaseModel):
    field: str
    reason: str
    valid_values: list[str] | None = None


class MCPErrorPayload(BaseModel):
    code: str
    message: str
    recovery_hint: str
    field_errors: list[FieldError] = Field(default_factory=list)
    next_commands: list[dict[str, Any]] = Field(default_factory=list)


class StructuredHTTPException(HTTPException):
    """HTTPException whose `detail` is an MCPErrorPayload JSON.

    LLM clients receive the same shape via FastMCP's content[].text
    wrapper, so the recovery_hint + field_errors + next_commands let
    the agent self-correct without human intervention. Reserve this
    for 4xx errors that are recoverable; leave 5xx + 422-validation
    on their FastAPI defaults.
    """

    def __init__(
        self,
        status_code: int,
        *,
        code: str,
        message: str,
        recovery_hint: str,
        field_errors: list[FieldError] | None = None,
        next_commands: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = MCPErrorPayload(
            code=code,
            message=message,
            recovery_hint=recovery_hint,
            field_errors=field_errors or [],
            next_commands=next_commands or [],
        )
        super().__init__(
            status_code=status_code,
            detail=payload.model_dump(mode="json"),
        )
