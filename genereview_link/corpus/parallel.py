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
from genereview_link.corpus.nxml import NxmlParseError, parse_and_chunk_one
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import SideData

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RawNxml:
    nbk_id: str
    relpath: str
    raw: bytes


def _iter_tarball(path: Path) -> Iterator[_RawNxml]:
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            if not member.isfile() or not member.name.endswith(".nxml"):
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            data = fh.read()
            yield _RawNxml(nbk_id="", relpath=member.name, raw=data)
            del data
            # nbk_id is resolved later via short-name + sidedata


def _worker_parse_chunk(
    raw_bytes: bytes,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
) -> tuple[ChapterRecord, list[PassageRecord]] | None:
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


async def parse_pipeline(
    tarball_path: Path,
    sidedata: SideData,
    *,
    parse_workers: int | None = None,
) -> AsyncIterator[tuple[ChapterRecord, list[PassageRecord]]]:
    """Yield (chapter, passages) per chapter; per-chapter independent."""
    parse_workers = parse_workers or settings.INGEST_PARSE_WORKERS
    loop = asyncio.get_running_loop()
    nbk_by_short = {v: k for k, v in sidedata.short_name_by_nbk.items()}

    with ProcessPoolExecutor(max_workers=parse_workers) as executor:
        in_flight: list[asyncio.Future[tuple[ChapterRecord, list[PassageRecord]] | None]] = []
        for raw in _iter_tarball(tarball_path):
            short_name = Path(raw.relpath).stem
            nbk_id = nbk_by_short.get(short_name, "")
            if not nbk_id:
                logger.warning("no NBK id for short_name=%s; skipping", short_name)
                continue
            fut: asyncio.Future[tuple[ChapterRecord, list[PassageRecord]] | None] = (
                loop.run_in_executor(
                    executor,
                    _worker_parse_chunk,
                    raw.raw,
                    nbk_id,
                    short_name,
                    raw.relpath,
                )
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
                    yield result
        for fut in asyncio.as_completed(in_flight):
            result = await fut
            if result is None:
                continue
            yield result


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
            "table_id",
            "table_data",
        ),
    )
