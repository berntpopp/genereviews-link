"""GeneReviewRepository.get_table integration tests."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Seed: one chapter with one table passage and one narrative passage.
_SEED_SQL_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKTBL', 'TBL', 'TableTest Gene Overview', null,
        ARRAY['TBL1'], ARRAY['600999']::text[], ARRAY['T. Author']::text[],
        'NBKTBL.xml', '2026-01-01', DATE '2025-07-01')
"""

_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version,
     passage_type, table_id, table_data)
values
    (
        'NBKTBL', 'NBKTBL:0001', 'diagnosis', 'Diagnosis > Table 1',
        1, 0,
        'Table: Variant classes',
        'h_tbl1', 24, 4, '2026-01-01',
        'table', 't1',
        '{"caption": "Variant classes", "header": ["Variant class", "Count"], "rows": [["Pathogenic", "12"], ["VUS", "3"]]}'::jsonb
    ),
    (
        'NBKTBL', 'NBKTBL:0002', 'summary', 'Summary',
        1, 1,
        'Narrative passage text.',
        'h_narr', 23, 4, '2026-01-01',
        'narrative', null, null
    )
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


async def test_get_table_returns_structured_rows(pool: asyncpg.Pool) -> None:
    """get_table returns a TableRow with correct header and rows."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    table = await repo.get_table("NBKTBL", "t1")
    assert table is not None
    assert table.nbk_id == "NBKTBL"
    assert table.table_id == "t1"
    assert table.caption == "Variant classes"
    assert table.header == ["Variant class", "Count"]
    assert len(table.rows) == 2
    assert table.rows[0] == ["Pathogenic", "12"]
    assert table.rows[1] == ["VUS", "3"]


async def test_get_table_passage_id_and_section(pool: asyncpg.Pool) -> None:
    """get_table populates passage_id, section, and heading_path."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    table = await repo.get_table("NBKTBL", "t1")
    assert table is not None
    assert table.passage_id == "NBKTBL:0001"
    assert table.section == "diagnosis"
    assert table.heading_path == "Diagnosis > Table 1"


async def test_get_table_unknown_table_returns_none(pool: asyncpg.Pool) -> None:
    """Unknown table_id returns None."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    result = await repo.get_table("NBKTBL", "t999")
    assert result is None


async def test_get_table_unknown_chapter_returns_none(pool: asyncpg.Pool) -> None:
    """Unknown nbk_id returns None."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    result = await repo.get_table("NBK0000000", "t1")
    assert result is None


async def test_get_table_narrative_passage_not_returned(pool: asyncpg.Pool) -> None:
    """A narrative passage is not returned even if table_id matches by coincidence."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    # passage_type='narrative' for NBKTBL:0002, so it won't match
    result = await repo.get_table("NBKTBL", "t2")
    assert result is None


async def test_list_table_ids_returns_known_tables(pool: asyncpg.Pool) -> None:
    """list_table_ids returns the seeded table ID."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    ids = await repo.list_table_ids("NBKTBL")
    assert ids == ["t1"]


async def test_list_table_ids_empty_for_unknown_chapter(pool: asyncpg.Pool) -> None:
    """list_table_ids returns empty list for unknown chapter."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    ids = await repo.list_table_ids("NBK0000000")
    assert ids == []
