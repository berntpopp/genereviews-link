"""ProcessPool + asyncio.Queue plumbing for the parse → chunk → write fan-out.

stages 4-6 of the ingest pipeline:
    tarfile stream → raw_nxml_queue → ProcessPool → record_queue → COPY writers
"""

from __future__ import annotations

import asyncio
import json
import logging
import tarfile
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from genereview_link.config import settings
from genereview_link.corpus.nxml import ChapterIngestAudit, NxmlParseError, parse_and_chunk_one
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import SideData
from genereview_link.download_guard import ResponseTooLargeError

# Resource ceilings for untrusted tarball members (F-05). A GeneReviews chapter
# .nxml is well under a few MB; these caps keep a single hostile/corrupt member
# (or a member-count/expanded-size bomb) from exhausting per-worker RAM.
MAX_TAR_MEMBERS = 100_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024  # 64 MiB per member
MAX_TOTAL_EXPANDED_BYTES = 8 * 1024**3  # 8 GiB across the whole archive

# Coverage threshold: if more than this fraction of a chapter's body text is
# unaccounted for after parsing, we log it at WARNING for operator review.
# 0.5% allows for whitespace normalization and the "_text(child) -> .strip()"
# leakage that's inherent in lxml's mixed-content traversal.
INGEST_COVERAGE_WARN_THRESHOLD = 0.005
INGEST_CROSS_REFERENCE_WARN_THRESHOLD = 0.25

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RawNxml:
    nbk_id: str
    relpath: str
    raw: bytes


def _iter_tarball(path: Path) -> Iterator[_RawNxml]:
    members_seen = 0
    total_expanded = 0
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            members_seen += 1
            if members_seen > MAX_TAR_MEMBERS:
                raise ResponseTooLargeError(f"tarball exceeds {MAX_TAR_MEMBERS} members")
            if not member.isfile() or not member.name.endswith(".nxml"):
                continue
            if member.size > MAX_MEMBER_BYTES:
                raise ResponseTooLargeError(
                    f"tar member {member.name} size {member.size} exceeds {MAX_MEMBER_BYTES} bytes"
                )
            fh = tf.extractfile(member)
            if fh is None:
                continue
            # Bounded read: never pull more than the cap into RAM even if the
            # member header understates its true size.
            data = fh.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ResponseTooLargeError(
                    f"tar member {member.name} expanded beyond {MAX_MEMBER_BYTES} bytes"
                )
            total_expanded += len(data)
            if total_expanded > MAX_TOTAL_EXPANDED_BYTES:
                raise ResponseTooLargeError(
                    f"tarball expanded beyond {MAX_TOTAL_EXPANDED_BYTES} bytes"
                )
            yield _RawNxml(nbk_id="", relpath=member.name, raw=data)
            del data
            # nbk_id is resolved later via short-name + sidedata


_ParseResult = tuple[ChapterRecord, list[PassageRecord], ChapterIngestAudit]


def _worker_parse_chunk(
    raw_bytes: bytes,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
) -> _ParseResult | None:
    try:
        return parse_and_chunk_one(
            raw_bytes,
            nbk_id=nbk_id,
            short_name=short_name,
            nxml_relpath=nxml_relpath,
        )
    except NxmlParseError as exc:
        logger.warning("parse failed nbk=%s relpath=%s: %s", nbk_id, nxml_relpath, exc)
        return None


def _log_audit(audit: ChapterIngestAudit) -> None:
    """Emit per-chapter conservation audit at INFO; WARN on shortfall."""
    extra = audit.as_log_extra()
    if audit.unaccounted_ratio > INGEST_COVERAGE_WARN_THRESHOLD:
        logger.warning(
            "ingest content-loss nbk=%s unaccounted_ratio=%.4f unknown_tags=%s",
            audit.nbk_id,
            audit.unaccounted_ratio,
            audit.unknown_tags_with_text,
            extra=extra,
        )
    elif audit.unknown_tags_with_text:
        logger.warning(
            "ingest unknown-tag nbk=%s tags=%s",
            audit.nbk_id,
            audit.unknown_tags_with_text,
            extra=extra,
        )
    else:
        logger.info(
            "ingest audit nbk=%s passages=%d captured=%d body=%d list=%d def_list=%d boxed=%d",
            audit.nbk_id,
            audit.passage_count,
            audit.captured_text_chars,
            audit.body_text_chars,
            audit.list_renders,
            audit.def_list_renders,
            audit.boxed_text_renders,
            extra=extra,
        )
    if audit.cross_reference_ratio > INGEST_CROSS_REFERENCE_WARN_THRESHOLD:
        logger.warning(
            "ingest role-distribution nbk=%s cross_reference_ratio=%.3f role_counts=%s",
            audit.nbk_id,
            audit.cross_reference_ratio,
            audit.role_counts,
            extra=extra,
        )


async def parse_pipeline(
    tarball_path: Path,
    sidedata: SideData,
    *,
    parse_workers: int | None = None,
) -> AsyncIterator[tuple[ChapterRecord, list[PassageRecord]]]:
    """Yield (chapter, passages) per chapter; per-chapter independent.

    Per-chapter ChapterIngestAudit is logged here (INFO if clean, WARN
    if content-loss threshold exceeded) and intentionally not surfaced
    to the consumer to keep the iterator signature stable for callers.
    """
    parse_workers = parse_workers or settings.INGEST_PARSE_WORKERS
    loop = asyncio.get_running_loop()
    nbk_by_short = {v: k for k, v in sidedata.short_name_by_nbk.items()}

    with ProcessPoolExecutor(max_workers=parse_workers) as executor:
        in_flight: list[asyncio.Future[_ParseResult | None]] = []
        for raw in _iter_tarball(tarball_path):
            short_name = Path(raw.relpath).stem
            nbk_id = nbk_by_short.get(short_name, "")
            if not nbk_id:
                logger.warning("no NBK id for short_name=%s; skipping", short_name)
                continue
            fut: asyncio.Future[_ParseResult | None] = loop.run_in_executor(
                executor,
                _worker_parse_chunk,
                raw.raw,
                nbk_id,
                short_name,
                raw.relpath,
            )
            in_flight.append(fut)
            if len(in_flight) >= parse_workers * 2:
                done_set, pending_set = await asyncio.wait(
                    in_flight, return_when=asyncio.FIRST_COMPLETED
                )
                in_flight = list(pending_set)
                for d in done_set:
                    result = await d
                    if result is None:
                        continue
                    chapter, passages, audit = result
                    _log_audit(audit)
                    yield chapter, passages
        for fut in asyncio.as_completed(in_flight):
            result = await fut
            if result is None:
                continue
            chapter, passages, audit = result
            _log_audit(audit)
            yield chapter, passages


async def copy_chapters(
    conn: asyncpg.Connection,
    chapters: list[ChapterRecord],
    *,
    corpus_version: str,
) -> None:
    records = [
        (
            c.nbk_id,
            c.short_name,
            c.title,
            c.pubmed_id,
            list(c.gene_symbols),
            list(c.omim_ids),
            c.authors,
            c.initial_pub_date,
            c.last_updated_date,
            corpus_version,
            c.nxml_relpath,
            "{}",  # raw_metadata default
            list(c.primary_gene_symbols),
        )
        for c in chapters
    ]
    await conn.copy_records_to_table(
        "genereview_chapters",
        records=records,
        columns=(
            "nbk_id",
            "short_name",
            "title",
            "pubmed_id",
            "gene_symbols",
            "omim_ids",
            "authors",
            "initial_pub_date",
            "last_updated_date",
            "corpus_version",
            "nxml_relpath",
            "raw_metadata",
            "primary_gene_symbols",
        ),
    )


async def copy_passages(
    conn: asyncpg.Connection,
    passages: list[PassageRecord],
    *,
    corpus_version: str,
) -> None:
    records = [
        (
            p.nbk_id,
            p.passage_id,
            p.chapter_section,
            p.heading_path,
            p.section_level,
            p.chunk_index,
            p.text,
            p.text_hash,
            p.char_count,
            p.token_estimate,
            corpus_version,
            p.passage_type,
            p.passage_role,
            p.table_id,
            json.dumps(p.table_data) if p.table_data is not None else None,
        )
        for p in passages
    ]
    await conn.copy_records_to_table(
        "genereview_passages",
        records=records,
        columns=(
            "nbk_id",
            "passage_id",
            "chapter_section",
            "heading_path",
            "section_level",
            "chunk_index",
            "text",
            "text_hash",
            "char_count",
            "token_estimate",
            "corpus_version",
            "passage_type",
            "passage_role",
            "table_id",
            "table_data",
        ),
    )
