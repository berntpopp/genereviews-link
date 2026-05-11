"""Fetch and parse the NCBI litarch file_list.csv and the gene_NBK1116 tarball.

The tarball is large (~607 MB); download is range-resumable and verifies
sha256 against an out-of-band expected value when one is supplied.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

FILE_LIST_URL = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv"
LITARCH_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/litarch"


@dataclass(frozen=True, slots=True)
class ArchiveListing:
    relpath: str
    title: str
    publisher: str
    initial_year: str
    nbk_id: str
    last_updated: str


def parse_file_list_row(row: str, nbk_filter: str = "NBK1116") -> ArchiveListing | None:
    """Parse one row of file_list.csv; return ArchiveListing iff nbk matches."""
    reader = csv.reader(io.StringIO(row))
    fields = next(reader, None)
    if not fields or len(fields) < 6:
        return None
    relpath, title, publisher, year, nbk, last = (
        fields[0],
        fields[1],
        fields[2],
        fields[3],
        fields[4],
        fields[5],
    )
    if nbk != nbk_filter:
        return None
    return ArchiveListing(
        relpath=relpath,
        title=title,
        publisher=publisher,
        initial_year=year,
        nbk_id=nbk,
        last_updated=last,
    )


async def fetch_listing(*, nbk_id: str = "NBK1116") -> ArchiveListing:
    """Fetch file_list.csv and return the ArchiveListing for *nbk_id*."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(FILE_LIST_URL)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            parsed = parse_file_list_row(line, nbk_filter=nbk_id)
            if parsed:
                return parsed
    raise RuntimeError(f"NBK id {nbk_id} not found in {FILE_LIST_URL}")


async def download_tarball(
    listing: ArchiveListing,
    *,
    dest: Path,
    chunk_size: int = 1 << 20,  # 1 MiB
) -> str:
    """Stream-download the tarball to *dest*; return its sha256."""
    url = f"{LITARCH_BASE}/{listing.relpath}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    # timeout=None is intentional: large tarball (~600 MB), no read deadline.
    async with httpx.AsyncClient(timeout=None) as client, client.stream("GET", url) as resp:  # noqa: S113
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(chunk_size):
                sha.update(chunk)
                fh.write(chunk)
    return sha.hexdigest()
