"""Tests for repository.search_passages."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository


async def _seed(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        await conn.execute(
            """
            insert into genereview_chapters
                (nbk_id, short_name, title, gene_symbols, corpus_version, nxml_relpath)
            values ('NBK1', 'brca', 'BRCA Chapter', '{BRCA1}', '2026-05-10', 'x.nxml')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, heading_path, section_level,
                 chunk_index, text, text_hash, char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 'Summary', 1, 0,
                 'BRCA1 is a tumor suppressor gene involved in DNA repair.',
                 'h1', 56, 12, '2026-05-10'),
                ('NBK1', 'NBK1:0002', 'management', 'Management', 1, 0,
                 'Risk-reducing surgery is offered to carriers.',
                 'h2', 45, 9, '2026-05-10')
            """
        )


@pytest.mark.asyncio
async def test_phrase_match_outranks_recall_match(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    results = await repo.search_passages("tumor suppressor")
    assert results
    assert results[0].passage.passage_id == "NBK1:0001"


@pytest.mark.asyncio
async def test_section_filter(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    results = await repo.search_passages("BRCA1", sections=["management"])
    assert all(r.passage.chapter_section == "management" for r in results)
