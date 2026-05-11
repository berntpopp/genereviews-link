"""End-to-end ingest against a mini 3-chapter tarball."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from genereview_link.corpus.archive import ArchiveListing
from genereview_link.corpus.parallel import copy_chapters, copy_passages, parse_pipeline
from genereview_link.corpus.pipeline import (
    atomic_swap,
    cleanup_old,
    prepare_staging,
    record_corpus_version_start,
)
from genereview_link.corpus.records import ChapterRecord
from genereview_link.corpus.sidedata import load_sidedata
from genereview_link.db.migrate import apply_control_migrations

FIXTURE_TARBALL = Path(__file__).parent.parent / "fixtures" / "bundles" / "mini.tar.gz"
FIXTURE_SIDEDATA = Path(__file__).parent.parent / "fixtures" / "sidedata"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_ingest_against_mini_tarball(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await prepare_staging(pool)

    listing = ArchiveListing(
        relpath="ca/84/gene_NBK1116.tar.gz",
        title="GeneReviews",
        publisher="UW",
        initial_year="1993",
        nbk_id="NBK1116",
        last_updated="2026-05-10 03:32:37",
    )
    version = await record_corpus_version_start(
        pool,
        listing=listing,
        tarball_sha256="deadbeef" * 8,
        size=FIXTURE_TARBALL.stat().st_size,
    )

    sidedata = load_sidedata(FIXTURE_SIDEDATA)

    chapter_count = 0
    passage_count = 0
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview_staging, public")
        async for chapter, passages in parse_pipeline(FIXTURE_TARBALL, sidedata, parse_workers=2):
            enriched = ChapterRecord(
                nbk_id=chapter.nbk_id,
                short_name=chapter.short_name,
                title=chapter.title,
                pubmed_id=chapter.pubmed_id,
                gene_symbols=sidedata.gene_symbols.get(chapter.nbk_id, ()),
                omim_ids=sidedata.omim_ids.get(chapter.nbk_id, ()),
                authors=chapter.authors,
                initial_pub_date=chapter.initial_pub_date,
                last_updated_date=chapter.last_updated_date,
                nxml_relpath=chapter.nxml_relpath,
                raw_metadata={},
            )
            await copy_chapters(conn, [enriched], corpus_version=version)
            await copy_passages(conn, passages, corpus_version=version)
            chapter_count += 1
            passage_count += len(passages)

    assert chapter_count >= 2

    await atomic_swap(pool, new_version=version, chapter_count=chapter_count)
    await cleanup_old(pool)

    async with pool.acquire() as conn:
        in_genereview = await conn.fetchval("select count(*) from genereview.genereview_chapters")
        assert in_genereview == chapter_count
