"""GeneReviewRepository.get_passage_window end-to-end behaviour (integration)."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Seed: one chapter with 6 passages:
#   section 'summary'    => chunk_index 0..2 (3 passages)
#   section 'management' => chunk_index 3..5 (3 passages)
_SEED_SQL_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKWIN', 'WG', 'Window Test Chapter', 99,
        ARRAY['WG'], ARRAY[]::text[], ARRAY[]::text[],
        'NBKWIN.xml', '2026-01-01', DATE '2025-12-01')
"""

_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version)
values
    ('NBKWIN', 'NBKWIN:0001', 'summary', 'Summary', 1, 0, 'Summary passage 0', 'h0', 17, 3, '2026-01-01'),
    ('NBKWIN', 'NBKWIN:0002', 'summary', 'Summary', 1, 1, 'Summary passage 1', 'h1', 17, 3, '2026-01-01'),
    ('NBKWIN', 'NBKWIN:0003', 'summary', 'Summary', 1, 2, 'Summary passage 2', 'h2', 17, 3, '2026-01-01'),
    ('NBKWIN', 'NBKWIN:0004', 'management', 'Management', 1, 3, 'Management passage 0', 'h3', 20, 3, '2026-01-01'),
    ('NBKWIN', 'NBKWIN:0005', 'management', 'Management', 1, 4, 'Management passage 1', 'h4', 20, 3, '2026-01-01'),
    ('NBKWIN', 'NBKWIN:0006', 'management', 'Management', 1, 5, 'Management passage 2', 'h5', 20, 3, '2026-01-01')
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


async def test_get_passage_window_returns_focal(pool: asyncpg.Pool) -> None:
    """Focal passage is returned correctly."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBKWIN:0002", before=1, after=1, cross_sections=False
    )
    assert focal is not None
    assert focal.passage_id == "NBKWIN:0002"
    assert focal.chapter_section == "summary"


async def test_get_passage_window_section_bounded(pool: asyncpg.Pool) -> None:
    """Section-bounded fetch keeps all neighbors in the same section."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    # NBKWIN:0002 is middle of 'summary' (chunk_index 1)
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBKWIN:0002", before=2, after=2, cross_sections=False
    )
    assert focal is not None
    assert focal.passage_id == "NBKWIN:0002"
    assert all(p.chapter_section == "summary" for p in before)
    assert all(p.chapter_section == "summary" for p in after)
    # Only one passage before (chunk_index 0) in same section
    assert len(before) == 1
    assert before[0].passage_id == "NBKWIN:0001"
    # Only one passage after (chunk_index 2) in same section
    assert len(after) == 1
    assert after[0].passage_id == "NBKWIN:0003"


async def test_get_passage_window_before_in_ascending_order(pool: asyncpg.Pool) -> None:
    """before_rows must be returned in ascending chunk_index order."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    # Use NBKWIN:0003 (chunk_index 2) — two predecessors in same section
    focal, before, after, _, _ = await repo.get_passage_window(
        "NBKWIN:0003", before=2, after=0, cross_sections=False
    )
    assert focal is not None
    chunk_indices = [p.chunk_index for p in before]
    assert chunk_indices == sorted(chunk_indices), "before rows must be ascending"
    assert chunk_indices == [0, 1]


async def test_get_passage_window_at_section_boundary_has_more_false(pool: asyncpg.Pool) -> None:
    """First passage in a section: before is empty and has_more_before is False."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBKWIN:0001", before=2, after=0, cross_sections=False
    )
    assert focal is not None
    assert before == []
    assert more_before is False


async def test_get_passage_window_has_more_after_true(pool: asyncpg.Pool) -> None:
    """has_more_after is True when more passages exist beyond the after window."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    # NBKWIN:0004 (chunk_index 3, 'management') with after=1: NBKWIN:0005 fits, NBKWIN:0006 is extra
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBKWIN:0004", before=0, after=1, cross_sections=False
    )
    assert focal is not None
    assert len(after) == 1
    assert after[0].passage_id == "NBKWIN:0005"
    assert more_after is True


async def test_get_passage_window_cross_sections_sees_other_section(pool: asyncpg.Pool) -> None:
    """cross_sections=True allows neighbors from adjacent sections."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    # NBKWIN:0003 is last in 'summary' (chunk_index 2); with cross_sections=True
    # after should include NBKWIN:0004 (management, chunk_index 3)
    focal, before, after, _, more_after = await repo.get_passage_window(
        "NBKWIN:0003", before=0, after=1, cross_sections=True
    )
    assert focal is not None
    assert len(after) == 1
    assert after[0].passage_id == "NBKWIN:0004"
    assert after[0].chapter_section == "management"


async def test_get_passage_window_unknown_returns_none(pool: asyncpg.Pool) -> None:
    """Unknown passage_id returns None focal and empty lists."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBKWIN:9999", before=2, after=2, cross_sections=False
    )
    assert focal is None
    assert before == []
    assert after == []
    assert more_before is False
    assert more_after is False
