"""GeneReviewRepository.get_passage end-to-end behaviour (integration)."""

from __future__ import annotations

import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_get_passage_returns_known_row(pool):
    """Use a seeded fixture chapter + passage; not the prod corpus."""
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.genereview_corpus_version "
            "(version, file_list_etag, tarball_sha256, tarball_size_bytes, "
            " ingest_started_at, ingest_status, is_active) "
            "values ('2026-01-01','etag','sha',0,now(),'completed',true)"
        )
        await conn.execute(
            "insert into genereview.genereview_chapters "
            "(nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids, "
            " authors, nxml_relpath, corpus_version, last_updated_date) "
            "values ('NBKTEST','TG','Test Chapter Title', 99, "
            "        ARRAY['TG'], ARRAY[]::text[], ARRAY[]::text[], "
            "        'NBKTEST.xml', '2026-01-01', DATE '2025-12-01')"
        )
        await conn.execute(
            "insert into genereview.genereview_passages "
            "(nbk_id, passage_id, chapter_section, heading_path, "
            " section_level, chunk_index, text, text_hash, "
            " char_count, token_estimate, corpus_version) "
            "values ('NBKTEST','NBKTEST:0001','management', "
            "        'Management > Treatment of Manifestations', "
            "        2, 1, 'sample passage text', 'h', 19, 4, '2026-01-01')"
        )

    repo = GeneReviewRepository(pool)
    row = await repo.get_passage("NBKTEST:0001")

    assert row is not None
    assert row.passage_id == "NBKTEST:0001"
    assert row.nbk_id == "NBKTEST"
    assert row.chapter_title == "Test Chapter Title"
    assert str(row.chapter_last_updated) == "2025-12-01"
    assert row.chapter_section == "management"
    assert row.heading_path == "Management > Treatment of Manifestations"
    assert row.text == "sample passage text"
    assert row.gene_symbols == ("TG",)


async def test_get_passage_returns_none_for_unknown(pool):
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    repo = GeneReviewRepository(pool)
    assert await repo.get_passage("NBK9999:9999") is None
