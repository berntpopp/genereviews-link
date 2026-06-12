"""Orchestrate the 9-stage ingest pipeline against an asyncpg pool.

Stages 0, 8, 9 mutate the control schema. Stages 4-6 use parallel.py.
Stage 7 (embeddings) is in retrieval/embeddings.py + ingest/orchestrator.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg

from genereview_link.corpus.archive import ArchiveListing, download_tarball, fetch_listing
from genereview_link.corpus.nxml import extract_primary_gene_symbols
from genereview_link.corpus.parallel import copy_chapters, copy_passages, parse_pipeline
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import load_sidedata
from genereview_link.db.migrate import apply_data_migrations

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    corpus_version: str
    chapter_count: int
    passage_count: int
    skipped_chapters: int


async def prepare_staging(pool: asyncpg.Pool) -> None:
    """Stage 0: drop and recreate the genereview_staging schema."""
    async with pool.acquire() as conn:
        await conn.execute("drop schema if exists genereview_staging cascade")
    await apply_data_migrations(pool, schema="genereview_staging")


async def record_corpus_version_start(
    pool: asyncpg.Pool, *, listing: ArchiveListing, tarball_sha256: str, size: int
) -> str:
    """Insert a new corpus_version row; return the chosen version string."""
    base = listing.last_updated.split(" ")[0]  # "2026-05-10"
    async with pool.acquire() as conn:
        # pick next free -rN suffix for same-day re-ingest
        version = base
        existing = await conn.fetchval(
            "select 1 from public.genereview_corpus_version where version = $1", version
        )
        if existing:
            n = 2
            while await conn.fetchval(
                "select 1 from public.genereview_corpus_version where version = $1",
                f"{base}-r{n}",
            ):
                n += 1
            version = f"{base}-r{n}"
        await conn.execute(
            """
            insert into public.genereview_corpus_version
                (version, file_list_etag, tarball_sha256, tarball_size_bytes,
                 ingest_started_at, ingest_status, is_active)
            values ($1, $2, $3, $4, $5, 'in_progress', false)
            """,
            version,
            listing.last_updated,
            tarball_sha256,
            size,
            datetime.now(UTC),
        )
    return version


async def atomic_swap(
    pool: asyncpg.Pool,
    *,
    new_version: str,
    chapter_count: int,
) -> None:
    """Stage 8: rename schemas + flip is_active in a single transaction."""
    async with pool.acquire() as conn, conn.transaction():
        # find any existing active version
        existing = await conn.fetchval(
            "select version from public.genereview_corpus_version where is_active"
        )
        if existing:
            target = f"genereview_old_{existing.replace('-', '_').replace('.', '_')}"
            await conn.execute(f'alter schema genereview rename to "{target}"')
            # Remove the old genereview:* migration records so the rename-rewrite
            # below (genereview_staging:* → genereview:*) does not hit a unique
            # constraint violation on the primary key (namespace, version).
            await conn.execute(
                "delete from public.schema_migrations "
                "where namespace = 'data' and version like 'genereview:%'"
            )
        else:
            # First ingest: drop the empty genereview schema that `db migrate`
            # provisioned so the staging rename below can land on a clean name.
            # Also clear its data-migration records — apply_data_migrations is
            # idempotent by qualified version, and we just dropped the schema.
            await conn.execute("drop schema if exists genereview cascade")
            await conn.execute(
                "delete from public.schema_migrations "
                "where namespace = 'data' and version like 'genereview:%'"
            )
        await conn.execute("alter schema genereview_staging rename to genereview")
        # The newly-active schema's data-migration records still say
        # 'genereview_staging:*'; rewrite them so future apply_data_migrations
        # invocations against 'genereview' see them as applied.
        await conn.execute(
            "update public.schema_migrations "
            "set version = replace(version, 'genereview_staging:', 'genereview:') "
            "where namespace = 'data' and version like 'genereview_staging:%'"
        )
        await conn.execute(
            "update public.genereview_corpus_version set is_active = false where is_active"
        )
        await conn.execute(
            """
            update public.genereview_corpus_version
               set is_active = true,
                   ingest_status = 'completed',
                   ingest_finished_at = $1,
                   chapter_count = $2
             where version = $3
            """,
            datetime.now(UTC),
            chapter_count,
            new_version,
        )


async def cleanup_old(pool: asyncpg.Pool, *, retain: int = 2) -> int:
    """Stage 9: drop genereview_old_* schemas beyond retention."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select schema_name
              from information_schema.schemata
             where schema_name like 'genereview_old_%'
             order by schema_name desc
            """
        )
    dropped = 0
    if len(rows) <= retain:
        return 0
    for row in rows[retain:]:
        async with pool.acquire() as conn:
            await conn.execute(f'drop schema "{row["schema_name"]}" cascade')
        dropped += 1
    return dropped


async def run_full_ingest(
    pool: asyncpg.Pool,
    *,
    work_dir: Path | None = None,
) -> IngestResult:
    """End-to-end stages 0-9 (excluding embeddings, which run separately)."""
    listing = await fetch_listing()
    with TemporaryDirectory(dir=work_dir) as td:
        td_path = Path(td)
        tarball = td_path / "gene_NBK1116.tar.gz"
        logger.info("downloading %s …", listing.relpath)
        sha = await download_tarball(listing, dest=tarball)
        # sidedata: download the three files alongside
        sidedata_dir = td_path / "sidedata"
        sidedata_dir.mkdir()
        await _download_sidedata(sidedata_dir)
        sidedata = load_sidedata(sidedata_dir)

        await prepare_staging(pool)
        version = await record_corpus_version_start(
            pool,
            listing=listing,
            tarball_sha256=sha,
            size=tarball.stat().st_size,
        )

        chapter_count = 0
        passage_count = 0
        chapter_buf: list[ChapterRecord] = []
        passage_buf: list[PassageRecord] = []
        batch_size = 50

        async for chapter, passages in parse_pipeline(tarball, sidedata):
            # apply sidedata joins
            sidedata_gs = sidedata.gene_symbols.get(chapter.nbk_id, ())
            chapter = ChapterRecord(
                nbk_id=chapter.nbk_id,
                short_name=chapter.short_name,
                title=chapter.title,
                pubmed_id=chapter.pubmed_id,
                gene_symbols=sidedata_gs,
                omim_ids=sidedata.omim_ids.get(chapter.nbk_id, ()),
                authors=chapter.authors,
                initial_pub_date=chapter.initial_pub_date,
                last_updated_date=chapter.last_updated_date,
                nxml_relpath=chapter.nxml_relpath,
                raw_metadata={},
                primary_gene_symbols=extract_primary_gene_symbols(chapter.title, sidedata_gs),
            )
            chapter_buf.append(chapter)
            passage_buf.extend(passages)
            chapter_count += 1
            passage_count += len(passages)
            if len(chapter_buf) >= batch_size:
                await _flush(pool, chapter_buf, passage_buf, version)
                chapter_buf.clear()
                passage_buf.clear()
        if chapter_buf:
            await _flush(pool, chapter_buf, passage_buf, version)

        await atomic_swap(pool, new_version=version, chapter_count=chapter_count)
        await cleanup_old(pool)

    return IngestResult(
        corpus_version=version,
        chapter_count=chapter_count,
        passage_count=passage_count,
        skipped_chapters=0,
    )


async def _flush(
    pool: asyncpg.Pool,
    chapters: list[ChapterRecord],
    passages: list[PassageRecord],
    version: str,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        # ``set local`` only takes effect inside a transaction. Without the
        # transaction wrapper, the COPY targets fall back to the connection's
        # default search_path (the user's own ``genereview`` schema), and the
        # subsequent atomic_swap drops that schema thinking it is the empty
        # migrate-bootstrap state — destroying the freshly-ingested rows.
        await conn.execute("set local search_path to genereview_staging, public")
        await copy_chapters(conn, chapters, corpus_version=version)
        await copy_passages(conn, passages, corpus_version=version)


async def _download_sidedata(target: Path) -> None:
    import httpx

    base = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
    files = (
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        for name in files:
            resp = await client.get(f"{base}/{name}")
            resp.raise_for_status()
            (target / name).write_bytes(resp.content)
