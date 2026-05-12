"""Cached set of indexed HGNC symbols for fast validation + fuzzy matching."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from rapidfuzz import fuzz, process


@dataclass(frozen=True, slots=True)
class GeneIndex:
    """Immutable set of indexed HGNC gene symbols with fuzzy close-match support."""

    symbols: frozenset[str]

    def is_indexed(self, symbol: str) -> bool:
        """Return True if *symbol* is present in the indexed set."""
        return symbol in self.symbols

    def close_matches(
        self,
        symbol: str,
        *,
        limit: int = 3,
        score_cutoff: float = 70.0,
    ) -> list[str]:
        """Return up to *limit* close matches above *score_cutoff*, ordered by score."""
        results = process.extract(
            symbol,
            self.symbols,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        return [match for match, _score, _idx in results]


async def load_gene_index(pool: asyncpg.Pool) -> GeneIndex:
    """Query the corpus for all distinct gene symbols and return a :class:`GeneIndex`."""
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        rows = await conn.fetch(
            "select distinct unnest(gene_symbols) as sym from genereview_chapters"
        )
    return GeneIndex(symbols=frozenset(r["sym"] for r in rows if r["sym"]))
