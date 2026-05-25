"""Cache TTL tests for GeneReviewService."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

import genereview_link.config as config_mod
import genereview_link.services.genereview_service as svc_mod


def test_cache_wrappers_use_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, int | None]] = []

    def spy_alru_cache(
        *,
        maxsize: int | None = None,
        ttl: int | None = None,
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        calls.append({"maxsize": maxsize, "ttl": ttl})

        def decorate(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            return func

        return decorate

    monkeypatch.setattr(svc_mod, "alru_cache", spy_alru_cache)
    monkeypatch.setattr(config_mod.settings, "CACHE_TTL_HOURS", 2)
    monkeypatch.setattr(config_mod.settings, "CACHE_SIZE", 99)

    svc_mod.GeneReviewService(client=object())  # type: ignore[arg-type]

    assert calls == [
        {"maxsize": 99, "ttl": 7200},
        {"maxsize": 99, "ttl": 7200},
        {"maxsize": 99, "ttl": 7200},
    ]


@pytest.mark.asyncio
async def test_get_genereview_uses_cache_for_repeated_gene() -> None:
    mock_client = AsyncMock()
    mock_client.search_genereview_pmid.return_value = "12345"
    mock_client.get_book_url_from_pmid.return_value = "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
    mock_client.scrape_genereview_book.return_value = {
        "title": {"content": "BRCA1 GeneReview"},
    }

    service = svc_mod.GeneReviewService(client=mock_client)  # type: ignore[arg-type]

    first = await service.get_genereview("BRCA1")
    second = await service.get_genereview("BRCA1")

    assert first.gene_symbol == "BRCA1"
    assert second.gene_symbol == "BRCA1"
    assert mock_client.search_genereview_pmid.await_count == 1
