"""Orchestrate the 9-stage ingest pipeline against an asyncpg pool.

Stages 0, 8, 9 mutate the control schema. Stages 4-6 use parallel.py.
Stage 7 (embeddings) is in retrieval/embeddings.py + ingest/orchestrator.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from genereview_link.corpus.archive import ArchiveListing
from genereview_link.corpus.nxml import extract_primary_gene_symbols
from genereview_link.corpus.parallel import copy_chapters, copy_passages, parse_pipeline
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import load_sidedata
from genereview_link.corpus.source_capture import load_offline_capture
from genereview_link.corpus.source_identity import SIDEDATA_FILES, validate_source_identity
from genereview_link.db.identifiers import quote_pg_identifier
from genereview_link.db.locks import CORPUS_INGEST_LOCK_KEY, CORPUS_WRITE_LOCK_KEY
from genereview_link.db.migrate import apply_data_migrations
from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    build_host_allowlist,
    make_url_guard,
    read_capped,
    write_exclusive_bytes,
)

logger = logging.getLogger(__name__)

MAX_SIDEDATA_BYTES = 64 * 1024 * 1024
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
    source_capture: dict[str, object] | None = None,
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
            async with conn.transaction():
                return await _record_corpus_version_start_locked(
                    conn,
                    listing=listing,
                    source=source,
                    exact_side_data=exact_side_data,
                    source_capture=source_capture,
                )
        finally:
            await conn.execute("select pg_advisory_unlock($1)", CORPUS_WRITE_LOCK_KEY)


async def _record_corpus_version_start_locked(
    conn: asyncpg.Connection,
    *,
    listing: ArchiveListing,
    source: Mapping[str, object],
    exact_side_data: Mapping[str, Mapping[str, str | int]],
    source_capture: dict[str, object] | None = None,
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
                 source_capture, ingest_started_at, ingest_status, is_active)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13,
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
        json.dumps(source_capture, sort_keys=True, separators=(",", ":"))
        if source_capture is not None
        else None,
        datetime.now(UTC),
    )
    if source_capture is not None:
        from genereview_link.corpus.computation_runs import record_ingest_run

        chapter_ids = source_capture.get("chapter_ids")
        if not isinstance(chapter_ids, list):
            raise ValueError("source capture chapter IDs are missing")
        await record_ingest_run(
            conn,
            corpus_version=version,
            source_capture=source_capture,
            expected_row_count=len(chapter_ids),
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
    archive: Path | None = None,
    side_data_dir: Path | None = None,
    source_metadata: Path | None = None,
    prior_manifest: Path | None = None,
    prior_seal_manifest: Path | None = None,
) -> IngestResult:
    """Serialize the entire shared staging lifecycle under a distinct session lock."""
    offline = (archive, side_data_dir, source_metadata, prior_manifest, prior_seal_manifest)
    if not all(value is not None for value in offline):
        message = (
            "offline ingest requires archive, side-data directory, metadata, prior manifest, "
            "and prior seal manifest"
            if any(value is not None for value in offline)
            else "mutating ingest requires the complete retained offline source set"
        )
        raise ValueError(message)
    async with pool.acquire() as lock_connection:
        await lock_connection.execute("select pg_advisory_lock($1)", CORPUS_INGEST_LOCK_KEY)
        try:
            return await _run_full_ingest_locked(
                pool,
                archive=archive,
                side_data_dir=side_data_dir,
                source_metadata=source_metadata,
                prior_manifest=prior_manifest,
                prior_seal_manifest=prior_seal_manifest,
            )
        finally:
            await lock_connection.execute("select pg_advisory_unlock($1)", CORPUS_INGEST_LOCK_KEY)


async def _run_full_ingest_locked(
    pool: asyncpg.Pool,
    *,
    archive: Path | None = None,
    side_data_dir: Path | None = None,
    source_metadata: Path | None = None,
    prior_manifest: Path | None = None,
    prior_seal_manifest: Path | None = None,
) -> IngestResult:
    """End-to-end stages 0-9 (excluding embeddings, which run separately)."""
    offline = (archive, side_data_dir, source_metadata, prior_manifest, prior_seal_manifest)
    if any(value is not None for value in offline):
        if not all(value is not None for value in offline):
            raise ValueError(
                "offline ingest requires archive, side-data directory, metadata, prior manifest, "
                "and prior seal manifest"
            )
        assert (
            archive is not None
            and side_data_dir is not None
            and source_metadata is not None
            and prior_manifest is not None
            and prior_seal_manifest is not None
        )
        capture = load_offline_capture(
            source_metadata,
            archive=archive,
            side_data_dir=side_data_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal_manifest,
        )
        listing_data = capture["listing"]
        archive_data = capture["archive"]
        side_data_identity = capture["side_data"]
        assert isinstance(listing_data, Mapping)
        assert isinstance(archive_data, Mapping)
        assert isinstance(side_data_identity, Mapping)
        listing = ArchiveListing(
            relpath=str(listing_data["relpath"]),
            title="GeneReviews",
            publisher="NCBI",
            initial_year="1993",
            nbk_id="NBK1116",
            last_updated=str(listing_data["last_updated"]),
        )
        return await _ingest_files(
            pool,
            listing=listing,
            tarball=archive,
            sidedata_dir=side_data_dir,
            tarball_sha256=str(archive_data["sha256"]),
            side_data_identity=side_data_identity,
            source_capture=capture,
        )
    raise ValueError("mutating ingest requires the complete retained offline source set")


async def _ingest_files(
    pool: asyncpg.Pool,
    *,
    listing: ArchiveListing,
    tarball: Path,
    sidedata_dir: Path,
    tarball_sha256: str,
    side_data_identity: Mapping[str, Mapping[str, str | int]],
    source_capture: dict[str, object] | None,
) -> IngestResult:
    sidedata = load_sidedata(sidedata_dir)
    if source_capture is not None:
        expected_ids = source_capture.get("chapter_ids")
        if expected_ids != sorted(sidedata.short_name_by_nbk):
            raise ValueError("retained title mapping does not match captured chapter IDs")
    version = await record_corpus_version_start(
        pool,
        listing=listing,
        tarball_sha256=tarball_sha256,
        size=tarball.stat().st_size,
        side_data=side_data_identity,
        source_capture=source_capture,
    )
    await prepare_staging(pool)

    chapter_count = 0
    passage_count = 0
    chapter_ids: list[str] = []
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
        chapter_ids.append(chapter.nbk_id)
        passage_buf.extend(passages)
        chapter_count += 1
        passage_count += len(passages)
        if len(chapter_buf) >= batch_size:
            await _flush(pool, chapter_buf, passage_buf, version)
            chapter_buf.clear()
            passage_buf.clear()
    if chapter_buf:
        await _flush(pool, chapter_buf, passage_buf, version)

    if source_capture is not None and sorted(chapter_ids) != source_capture.get("chapter_ids"):
        raise ValueError("parsed archive chapter IDs do not match retained capture")
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
    """Fetch bounded side data for non-mutating capture tooling only."""
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
            write_exclusive_bytes(target / name, body)
            identity[name] = {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
    return identity
