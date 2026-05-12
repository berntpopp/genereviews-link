"""GeneReviewRepository.search_passages(brief=True) attaches ts_headline snippets."""

from __future__ import annotations

import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _seed(pool) -> None:
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
            "values ('NBKBR','BR','BRCA1 HBOC Test',99,ARRAY['BRCA1'],"
            "        ARRAY[]::text[], ARRAY[]::text[], 'x.xml','2026-01-01',"
            "        DATE '2025-12-01')"
        )
        await conn.execute(
            "insert into genereview.genereview_passages "
            "(nbk_id, passage_id, chapter_section, heading_path, "
            " section_level, chunk_index, text, text_hash, "
            " char_count, token_estimate, corpus_version) "
            "values ('NBKBR','NBKBR:0001','management','Management > X',"
            "        2, 1, 'BRCA1 risk-reducing mastectomy is an option "
            "for some women at elevated risk of breast cancer.',"
            "        'h', 100, 20, '2026-01-01')"
        )


async def test_search_passages_brief_returns_snippet(pool):
    await _seed(pool)

    repo = GeneReviewRepository(pool)
    rows = await repo.search_passages(
        "BRCA1 risk-reducing mastectomy",
        brief=True,
        limit=5,
    )
    assert rows, "expected at least one match"
    snippet = rows[0].snippet
    assert snippet is not None
    assert "**" in snippet, "expected bolded highlight markers"


async def test_search_passages_default_omits_snippet(pool):
    await _seed(pool)

    repo = GeneReviewRepository(pool)
    rows = await repo.search_passages("BRCA1 risk-reducing mastectomy", limit=5)
    assert rows
    assert rows[0].snippet is None
