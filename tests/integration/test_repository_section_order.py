"""GeneReviewRepository.get_section chunk_index ordering regression test."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Seed: one chapter with 5 passages in 'management', inserted in intentionally
# out-of-order chunk_index sequence (3, 1, 5, 2, 4) to prove the SQL ORDER BY
# clause restores the correct ascending order.
_SEED_SQL_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKORD', 'OG', 'Ordering Test Chapter', null,
        ARRAY['OG1'], ARRAY[]::text[], ARRAY[]::text[],
        'NBKORD.xml', '2026-01-01', DATE '2025-12-01')
"""

# Passages are inserted with chunk_index values 3, 1, 5, 2, 4 — deliberately
# out of order — to verify that get_section returns them sorted ascending.
_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version)
values
    ('NBKORD', 'NBKORD:0003', 'management', 'Management', 1, 3, 'Management passage 3', 'h3', 20, 3, '2026-01-01'),
    ('NBKORD', 'NBKORD:0001', 'management', 'Management', 1, 1, 'Management passage 1', 'h1', 20, 3, '2026-01-01'),
    ('NBKORD', 'NBKORD:0005', 'management', 'Management', 1, 5, 'Management passage 5', 'h5', 20, 3, '2026-01-01'),
    ('NBKORD', 'NBKORD:0002', 'management', 'Management', 1, 2, 'Management passage 2', 'h2', 20, 3, '2026-01-01'),
    ('NBKORD', 'NBKORD:0004', 'management', 'Management', 1, 4, 'Management passage 4', 'h4', 20, 3, '2026-01-01')
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


async def test_get_section_returns_passages_in_chunk_index_order(
    pool: asyncpg.Pool,
) -> None:
    """get_section must return passages sorted ascending by chunk_index.

    Passages are inserted out-of-order (chunk_index 3, 1, 5, 2, 4) and the
    test asserts the returned list is [1, 2, 3, 4, 5] — proving the SQL
    ORDER BY clause is in effect.
    """
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    rows = await repo.get_section("NBKORD", "management")
    assert len(rows) == 5, f"Expected 5 passages, got {len(rows)}"
    indices = [r.chunk_index for r in rows]
    assert indices == sorted(indices), f"Section ordering broken: {indices}"
    assert indices == [1, 2, 3, 4, 5], f"Unexpected chunk_index sequence: {indices}"
