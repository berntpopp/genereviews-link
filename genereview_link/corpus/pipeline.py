"""Orchestrate the 9-stage ingest pipeline against an asyncpg pool.

Stages 0, 8, 9 mutate the control schema. Stages 4-6 use parallel.py.
Stage 7 (embeddings) is in retrieval/embeddings.py + ingest/orchestrator.py.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
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
from genereview_link.corpus.source_identity import SIDEDATA_FILES, validate_source_identity
from genereview_link.db.identifiers import quote_pg_identifier
from genereview_link.db.locks import CORPUS_WRITE_LOCK_KEY
from genereview_link.db.migrate import apply_data_migrations
from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    build_host_allowlist,
    make_url_guard,
    read_capped,
)

logger = logging.getLogger(__name__)

# The three sidedata index files are small; cap each fail-closed so a hostile
# NCBI mirror cannot exhaust RAM via an oversized response.
MAX_SIDEDATA_BYTES = 64 * 1024 * 1024  # 64 MiB
SIDEDATA_DOWNLOAD_DEADLINE_SECONDS = 2 * 60.0


@dataclass(frozen=True, slots=True)
class IngestResult:
    corpus_version: str
    chapter_count: int
    passage_count: int
    skipped_chapters: int


async def prepare_staging(pool: asyncpg.Pool) -> None:
    """Stage 0: drop and recreate the genereview_staging schema."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        await conn.execute("drop schema if exists genereview_staging cascade")
        await conn.execute(
            "delete from public.schema_migrations "
            "where namespace = 'data' and version like 'genereview_staging:%'"
        )
    await apply_data_migrations(pool, schema="genereview_staging")


async def record_corpus_version_start(
    pool: asyncpg.Pool,
    *,
    listing: ArchiveListing,
    tarball_sha256: str,
    size: int,
    side_data: Mapping[str, Mapping[str, str | int]],
) -> str:
    """Insert a new corpus_version row; return the chosen version string."""
    source = validate_source_identity(
        {
            "listing_relpath": listing.relpath,
            "last_updated": listing.last_updated,
            "tarball": {"sha256": tarball_sha256, "size_bytes": size},
            "side_data": side_data,
        },
        tarball_sha256=tarball_sha256,
        last_updated=listing.last_updated,
    )
    exact_side_data = source["side_data"]
    assert isinstance(exact_side_data, dict)
    async with pool.acquire() as conn:
        await conn.execute("select pg_advisory_lock($1)", CORPUS_WRITE_LOCK_KEY)
        try:
            return await _record_corpus_version_start_locked(
                conn, listing=listing, source=source, exact_side_data=exact_side_data
            )
        finally:
            await conn.execute("select pg_advisory_unlock($1)", CORPUS_WRITE_LOCK_KEY)


async def _record_corpus_version_start_locked(
    conn: asyncpg.Connection,
    *,
    listing: ArchiveListing,
    source: Mapping[str, object],
    exact_side_data: Mapping[str, Mapping[str, str | int]],
) -> str:
    """Choose and insert a version while the database-wide writer lock is held."""
    base = str(source["last_updated"]).split(" ")[0]
    tarball = source["tarball"]
    if not isinstance(tarball, Mapping):
        raise ValueError("source tarball identity is incomplete")
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
                (version, listing_relpath, file_list_etag,
                 tarball_sha256, tarball_size_bytes,
                 sidedata_title_sha256, sidedata_title_size_bytes,
                 sidedata_genes_sha256, sidedata_genes_size_bytes,
                 sidedata_omim_sha256, sidedata_omim_size_bytes,
                 ingest_started_at, ingest_status, is_active)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    'in_progress', false)
        """,
        version,
        str(source["listing_relpath"]),
        str(source["last_updated"]),
        str(tarball["sha256"]),
        int(tarball["size_bytes"]),
        exact_side_data[SIDEDATA_FILES[0]]["sha256"],
        exact_side_data[SIDEDATA_FILES[0]]["size_bytes"],
        exact_side_data[SIDEDATA_FILES[1]]["sha256"],
        exact_side_data[SIDEDATA_FILES[1]]["size_bytes"],
        exact_side_data[SIDEDATA_FILES[2]]["sha256"],
        exact_side_data[SIDEDATA_FILES[2]]["size_bytes"],
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
        await conn.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        # find any existing active version
        existing = await conn.fetchval(
            "select version from public.genereview_corpus_version where is_active"
        )
        if existing:
            target = f"genereview_old_{existing.replace('-', '_').replace('.', '_')}"
            # Fail closed: validate the derived schema identifier before it is
            # interpolated into the rename DDL (quote_pg_identifier double-quotes).
            await conn.execute(f"alter schema genereview rename to {quote_pg_identifier(target)}")
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
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
            # Fail closed: validate the catalog-sourced schema name before the DDL.
            quoted = quote_pg_identifier(row["schema_name"])
            await conn.execute(f"drop schema {quoted} cascade")
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
        side_data_identity = await _download_sidedata(sidedata_dir)
        sidedata = load_sidedata(sidedata_dir)

        await prepare_staging(pool)
        version = await record_corpus_version_start(
            pool,
            listing=listing,
            tarball_sha256=sha,
            size=tarball.stat().st_size,
            side_data=side_data_identity,
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
        await conn.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        # ``set local`` only takes effect inside a transaction. Without the
        # transaction wrapper, the COPY targets fall back to the connection's
        # default search_path (the user's own ``genereview`` schema), and the
        # subsequent atomic_swap drops that schema thinking it is the empty
        # migrate-bootstrap state — destroying the freshly-ingested rows.
        await conn.execute("set local search_path to genereview_staging, public")
        await copy_chapters(conn, chapters, corpus_version=version)
        await copy_passages(conn, passages, corpus_version=version)


async def _download_sidedata(target: Path) -> dict[str, dict[str, str | int]]:
    import httpx

    base = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
    identity: dict[str, dict[str, str | int]] = {}
    hosts = build_host_allowlist(base)
    async with httpx.AsyncClient(
        timeout=STREAM_TIMEOUT,
        follow_redirects=False,
        event_hooks={"request": [make_url_guard(hosts)]},
    ) as client:
        for name in SIDEDATA_FILES:
            body = await read_capped(
                client,
                f"{base}/{name}",
                max_bytes=MAX_SIDEDATA_BYTES,
                deadline_seconds=SIDEDATA_DOWNLOAD_DEADLINE_SECONDS,
            )
            (target / name).write_bytes(body)
            identity[name] = {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
    return identity
