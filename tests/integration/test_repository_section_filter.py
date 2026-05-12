"""Integration test: get_section honours heading_path_contains."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SEED_SQL_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKHPC', 'hpc', 'HeadingPathContains', null,
        ARRAY['HPC1'], ARRAY[]::text[], ARRAY[]::text[],
        'NBKHPC.xml', '2026-01-01', DATE '2025-12-01')
"""

_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version)
values
    ('NBKHPC', 'NBKHPC:0000', 'management',
     'Management > Surveillance', 1, 0,
     'surveillance text', 'h0', 17, 2, '2026-01-01'),
    ('NBKHPC', 'NBKHPC:0001', 'management',
     'Management > Treatment > Risk-Reducing Surgery', 2, 1,
     'RRM/RRSO text', 'h1', 13, 2, '2026-01-01'),
    ('NBKHPC', 'NBKHPC:0002', 'management',
     'Management > Treatment > Risk-Reducing Surgery', 2, 2,
     'more surgery text', 'h2', 17, 2, '2026-01-01'),
    ('NBKHPC', 'NBKHPC:0003', 'management',
     'Management > Family Counseling', 1, 3,
     'counseling text', 'h3', 15, 2, '2026-01-01')
"""


async def _seed(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.genereview_corpus_version "
            "(version, file_list_etag, tarball_sha256, tarball_size_bytes, "
            " ingest_started_at, ingest_status, is_active) "
            "values ('2026-01-01','etag','sha',0,now(),'completed',true)"
        )
        await conn.execute(_SEED_SQL_CHAPTER)
        await conn.execute(_SEED_SQL_PASSAGES)


async def test_get_section_no_filter_returns_all_passages(pool: asyncpg.Pool) -> None:
    """Without a filter all 4 seeded passages are returned."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    rows = await repo.get_section("NBKHPC", "management")
    assert len(rows) == 4


async def test_get_section_filters_by_heading_path_contains(pool: asyncpg.Pool) -> None:
    """heading_path_contains narrows results to matching passages only."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    filtered = await repo.get_section(
        "NBKHPC", "management", heading_path_contains="Risk-Reducing Surgery"
    )
    assert len(filtered) == 2
    assert all("Risk-Reducing Surgery" in (r.heading_path or "") for r in filtered)


async def test_get_section_heading_path_contains_is_case_insensitive(pool: asyncpg.Pool) -> None:
    """ILIKE match must succeed regardless of letter case."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    ci = await repo.get_section(
        "NBKHPC", "management", heading_path_contains="risk-reducing surgery"
    )
    assert len(ci) == 2


async def test_get_section_heading_path_contains_no_match_returns_empty(
    pool: asyncpg.Pool,
) -> None:
    """A filter that matches nothing returns an empty list."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    none = await repo.get_section(
        "NBKHPC", "management", heading_path_contains="NoSuchSubsection"
    )
    assert len(none) == 0
