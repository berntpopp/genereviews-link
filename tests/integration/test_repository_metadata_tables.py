"""Integration test: get_chapter_metadata projects a tables list in source order."""

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
values ('NBKTBLLIST', 'tbllist', 'Tables List Test', null,
        ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[],
        'NBKTBLLIST.xml', '2026-01-01', DATE '2025-08-01')
"""

# 1 narrative + 2 tables, ordered by chunk_index
_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version,
     passage_type, table_id, table_data)
values
    ('NBKTBLLIST', 'NBKTBLLIST:0000', 'summary', null,
     1, 0, 'intro narrative', 'hash0', 16, 2, '2026-01-01',
     'narrative', null, null),
    ('NBKTBLLIST', 'NBKTBLLIST:0001', 'management', 'Management > Table 1',
     1, 1, 'Table 1 markdown body', 'hash1', 21, 4, '2026-01-01',
     'table', 'mgmt.T.first',
     '{"caption": "Table 1 — Risk-reducing surgery", "header": ["Variant", "Drug"], "rows": [["a", "b"]]}'::jsonb),
    ('NBKTBLLIST', 'NBKTBLLIST:0002', 'management', 'Management > Table 2',
     1, 2, 'Table 2 markdown body', 'hash2', 21, 4, '2026-01-01',
     'table', 'mgmt.T.second',
     '{"caption": "Table 2 — Followup", "header": ["Visit", "Frequency"], "rows": [["mri", "annual"]]}'::jsonb)
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


async def test_get_chapter_metadata_returns_tables_in_source_order(pool: asyncpg.Pool) -> None:
    """get_chapter_metadata projects tables list ordered by chunk_index."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKTBLLIST")
    assert meta is not None
    assert len(meta.tables) == 2
    assert [t.table_id for t in meta.tables] == ["mgmt.T.first", "mgmt.T.second"]
    assert meta.tables[0].caption.startswith("Table 1")
    assert meta.tables[0].section == "management"
    assert meta.tables[0].heading_path == "Management > Table 1"
    assert meta.tables[0].passage_id == "NBKTBLLIST:0001"


async def test_get_chapter_metadata_tables_empty_for_narrative_only(pool: asyncpg.Pool) -> None:
    """A chapter with only narrative passages returns an empty tables list."""
    await _seed(pool)
    # The NBKTBLLIST chapter has tables, so seed a clean narrative-only chapter
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into genereview.genereview_chapters
                (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                 authors, nxml_relpath, corpus_version, last_updated_date)
            values ('NBKNARRONLY', 'narr', 'Narrative Only Chapter', null,
                    ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[],
                    'NBKNARRONLY.xml', '2026-01-01', null)
            """
        )
        await conn.execute(
            """
            insert into genereview.genereview_passages
                (nbk_id, passage_id, chapter_section, heading_path,
                 section_level, chunk_index, text, text_hash,
                 char_count, token_estimate, corpus_version)
            values
                ('NBKNARRONLY', 'NBKNARRONLY:0001', 'summary', 'Summary',
                 1, 0, 'Only narrative', 'nh0', 14, 2, '2026-01-01')
            """
        )
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKNARRONLY")
    assert meta is not None
    assert meta.tables == ()
