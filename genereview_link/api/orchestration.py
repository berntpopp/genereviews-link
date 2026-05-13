"""Shared helpers for legacy orchestration routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import Request

from genereview_link.models.genereview_models import ResponseMeta
from genereview_link.retrieval.repository import GeneReviewRepository


@runtime_checkable
class VersionedResponse(Protocol):
    corpus_version: str | None
    meta: ResponseMeta


def get_optional_repository(request: Request) -> GeneReviewRepository | None:
    repo = getattr(request.app.state, "repository", None)
    return repo


def active_corpus_version(request: Request) -> str | None:
    return getattr(request.app.state, "corpus_version", None)


def live_corpus_version() -> str:
    return f"live:{datetime.now(UTC).isoformat()}"


def stamp_response_version(
    response: VersionedResponse,
    *,
    corpus_version: str | None,
) -> None:
    response.corpus_version = corpus_version
    response.meta.corpus_version = corpus_version
