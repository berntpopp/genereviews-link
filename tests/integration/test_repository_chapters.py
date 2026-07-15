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


async def _seed_defining(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        # CFTR: sole gene in NBK1250 (defining) + one of many in an overview (mention).
        # CLDN2: ONLY ever a mention in the multi-gene overview (no defining chapter).
        # SCN1A: defining via TITLE word-match even though it shares the chapter.
        await conn.execute(
            """
            insert into genereview_chapters
                (nbk_id, short_name, title, gene_symbols, omim_ids, corpus_version, nxml_relpath)
            values
                ('NBK1250', 'cf', 'Cystic Fibrosis', '{CFTR}', '{}', 'v', 'a'),
                ('NBK190101', 'pancr', 'Pancreatitis Overview',
                    '{CASR,CEL,CFTR,CLDN2,PRSS1}', '{}', 'v', 'b'),
                ('NBK1318', 'gefs', 'SCN1A Seizure Disorders',
                    '{SCN1A,ATP1A2,CACNA1A}', '{}', 'v', 'c')
            """
        )


@pytest.mark.asyncio
async def test_get_defining_chapter_by_gene_resolution(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed_defining(pool)
    repo = GeneReviewRepository(pool)

    # Sole-gene chapter wins over the multi-gene overview that merely mentions it.
    cftr = await repo.get_defining_chapter_by_gene("CFTR")
    assert cftr is not None and cftr.nbk_id == "NBK1250"

    # Title word-match makes SCN1A's own chapter its defining chapter.
    scn1a = await repo.get_defining_chapter_by_gene("SCN1A")
    assert scn1a is not None and scn1a.nbk_id == "NBK1318"

    # A gene that only ever appears as a MENTION in a multi-gene chapter has NO
    # defining chapter -> None (the route answers not_found, never the overview).
    assert await repo.get_defining_chapter_by_gene("CLDN2") is None


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
