"""Repository chapter and section fetcher tests."""

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
                (nbk_id, short_name, title, gene_symbols, omim_ids, corpus_version, nxml_relpath, pubmed_id)
            values
                ('NBK1', 'brca', 'BRCA Chapter', '{BRCA1,BRCA2}', '{113705}', '2026-05-10', 'x', '12345'),
                ('NBK2', 'brca-alt', 'BRCA Alternate Chapter', '{BRCA1}', '{}', '2026-05-10', 'y', '67890')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, chunk_index, text, text_hash,
                 char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 0, 'Summary text', 'h', 12, 3, '2026-05-10'),
                ('NBK1', 'NBK1:0002', 'summary', 1, 'More summary', 'h', 12, 3, '2026-05-10'),
                ('NBK1', 'NBK1:0003', 'diagnosis', 0, 'Diagnosis text', 'h', 14, 3, '2026-05-10')
            """
        )


@pytest.mark.asyncio
async def test_get_chapter_by_gene(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    chapter = await repo.get_chapter_by_gene("BRCA1")
    assert chapter is not None
    assert chapter.nbk_id == "NBK1"
    assert "BRCA2" in chapter.gene_symbols


@pytest.mark.asyncio
async def test_get_chapters_by_gene_returns_all_matches(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    chapters = await repo.get_chapters_by_gene("BRCA1")
    assert [chapter.pubmed_id for chapter in chapters] == ["12345", "67890"]


@pytest.mark.asyncio
async def test_get_section_returns_ordered(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    passages = await repo.get_section("NBK1", "summary")
    assert [p.chunk_index for p in passages] == [0, 1]


@pytest.mark.asyncio
async def test_get_chapter_by_pmid(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    chapter = await repo.get_chapter_by_pmid("12345")
    assert chapter is not None
    assert chapter.nbk_id == "NBK1"
