"""Backfill last_updated_date for all chapters in the live genereview DB.

This script re-parses each chapter's cached NXML from the local litarch
tarball and updates last_updated_date only when the stored value differs
from the newly-parsed value (idempotent).

Background:
    Task B1 (2026-05-12) found that the parser in genereview_link/corpus/nxml.py
    had the date-type preference inverted: it preferred <date date-type="revised">
    (a schema-metadata timestamp) over <date date-type="updated"> (the editorial
    content update timestamp visible on the NCBI GeneReviews web page).  Task 16
    applies the one-line fix.  This script backfills chapters already in the DB.

NXML source:
    NXMLs are stored inside ~/Downloads/gene_NBK1116.tar.gz — the litarch tarball
    downloaded during the initial corpus ingest.  Each chapter row in the DB has an
    nxml_relpath column (e.g. "gene_NBK1116/hemochromatosis.nxml") that directly
    maps to a member path inside the tarball.  We stream the tarball once, build an
    in-memory map of relpath -> raw NXML bytes, then iterate over all chapter rows
    and re-parse each.

Usage:
    DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview \\
        uv run python scripts/refresh_chapter_metadata_dates.py

    Optional env vars:
        TARBALL_PATH  - override default ~/Downloads/gene_NBK1116.tar.gz
"""

from __future__ import annotations

import logging
import os
import sys
import tarfile
from pathlib import Path

import asyncpg

# ---------------------------------------------------------------------------
# Import the parser.  The script is run from the repo root so the package is
# on sys.path via uv run / editable install.
# ---------------------------------------------------------------------------
from genereview_link.corpus.nxml import NxmlParseError, parse_and_chunk_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://genereview:genereview@127.0.0.1:5436/genereview",
)

_default_tarball = Path.home() / "Downloads" / "gene_NBK1116.tar.gz"
TARBALL_PATH = Path(os.environ.get("TARBALL_PATH", str(_default_tarball)))


# ---------------------------------------------------------------------------
# Tarball loader
# ---------------------------------------------------------------------------

def load_nxml_map(tarball: Path) -> dict[str, bytes]:
    """Stream the tarball and return a mapping of member-path -> raw NXML bytes.

    Only .nxml members are kept; the rest are skipped.
    """
    log.info("streaming tarball %s …", tarball)
    nxml_map: dict[str, bytes] = {}
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf:
            if not member.isfile() or not member.name.endswith(".nxml"):
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            nxml_map[member.name] = fh.read()
    log.info("loaded %d NXML files from tarball", len(nxml_map))
    return nxml_map


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

async def run() -> None:
    if not TARBALL_PATH.exists():
        log.error("tarball not found: %s", TARBALL_PATH)
        log.error(
            "set TARBALL_PATH env var to point to the local gene_NBK1116.tar.gz"
        )
        sys.exit(1)

    nxml_map = load_nxml_map(TARBALL_PATH)

    log.info("connecting to %s", DATABASE_URL)
    conn: asyncpg.Connection = await asyncpg.connect(DATABASE_URL)

    try:
        rows = await conn.fetch(
            "SELECT nbk_id, short_name, nxml_relpath, last_updated_date "
            "FROM genereview.genereview_chapters "
            "ORDER BY nbk_id"
        )
    finally:
        pass  # keep connection open for updates

    total = len(rows)
    updated = 0
    unchanged = 0
    null_date = 0
    skipped_no_nxml = 0

    log.info("scanning %d chapters …", total)

    for row in rows:
        nbk_id: str = row["nbk_id"]
        short_name: str = row["short_name"]
        nxml_relpath: str = row["nxml_relpath"]
        stored_date = row["last_updated_date"]  # datetime.date or None

        raw = nxml_map.get(nxml_relpath)
        if raw is None:
            log.warning("no NXML for %s at relpath=%s — skipping", nbk_id, nxml_relpath)
            skipped_no_nxml += 1
            continue

        try:
            chapter, _ = parse_and_chunk_one(
                raw,
                nbk_id=nbk_id,
                short_name=short_name,
                nxml_relpath=nxml_relpath,
            )
        except NxmlParseError as exc:
            log.warning("parse error for %s: %s — skipping", nbk_id, exc)
            skipped_no_nxml += 1
            continue

        new_date = chapter.last_updated_date

        if new_date is None:
            null_date += 1

        # UPDATE only when the value actually differs (idempotent).
        if new_date != stored_date:
            await conn.execute(
                "UPDATE genereview.genereview_chapters "
                "SET last_updated_date = $1 "
                "WHERE nbk_id = $2 "
                "  AND last_updated_date IS DISTINCT FROM $1",
                new_date,
                nbk_id,
            )
            log.info(
                "updated %s: %s -> %s",
                nbk_id,
                stored_date,
                new_date,
            )
            updated += 1
        else:
            unchanged += 1

    await conn.close()

    log.info("--- summary ---")
    log.info("chapters scanned  : %d", total)
    log.info("chapters updated  : %d", updated)
    log.info("chapters unchanged: %d", unchanged)
    log.info("chapters null date: %d", null_date)
    log.info("chapters skipped  : %d", skipped_no_nxml)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
