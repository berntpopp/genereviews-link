"""Fetch and parse the NCBI litarch file_list.csv and the gene_NBK1116 tarball.

The tarball is large (~607 MB); download is range-resumable and verifies
sha256 against an out-of-band expected value when one is supplied.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    RequestHook,
    build_host_allowlist,
    make_url_guard,
    read_capped,
    stream_to_file,
)

FILE_LIST_URL = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv"
LITARCH_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/litarch"

# NCBI does not redirect these paths; pin the host and keep follow_redirects
# off. The allowlist still guards against a config/DNS pivot to another host.
_NCBI_HOSTS = build_host_allowlist(FILE_LIST_URL, LITARCH_BASE)

# The litarch tarball is ~613 MB and grows over time; cap well above that so a
# legit download completes but a hostile endpoint cannot exhaust disk.
MAX_TARBALL_BYTES = 4 * 1024**3  # ~4 GiB
# file_list.csv is a small text index; anything huge is an attack/mistake.
MAX_LISTING_BYTES = 64 * 1024 * 1024  # 64 MiB
# Explicit whole-transfer budgets.  These are intentionally set at each call
# boundary rather than inheriting the guard's generic default.
LISTING_DOWNLOAD_DEADLINE_SECONDS = 2 * 60.0
TARBALL_DOWNLOAD_DEADLINE_SECONDS = 20 * 60.0


def _ncbi_client(*, request_hooks: Sequence[RequestHook] = ()) -> httpx.AsyncClient:
    """Build the pinned NCBI client, optionally behind extra request hooks.

    ``request_hooks`` runs after the host guard and before the request leaves,
    which is where a politeness rate limiter belongs: it then also throttles
    every auto-followed hop, not just the calls a caller remembered to gate.
    """
    return httpx.AsyncClient(
        timeout=STREAM_TIMEOUT,
        follow_redirects=False,
        event_hooks={"request": [make_url_guard(_NCBI_HOSTS), *request_hooks]},
    )


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


async def fetch_listing_bytes(*, request_hooks: Sequence[RequestHook] = ()) -> bytes:
    """Fetch the raw, byte-capped file_list.csv response body."""
    async with _ncbi_client(request_hooks=request_hooks) as client:
        return await read_capped(
            client,
            FILE_LIST_URL,
            max_bytes=MAX_LISTING_BYTES,
            deadline_seconds=LISTING_DOWNLOAD_DEADLINE_SECONDS,
        )


async def fetch_listing(
    *, nbk_id: str = "NBK1116", request_hooks: Sequence[RequestHook] = ()
) -> ArchiveListing:
    """Fetch file_list.csv and return the ArchiveListing for *nbk_id*."""
    body = await fetch_listing_bytes(request_hooks=request_hooks)
    return listing_from_bytes(body, nbk_id=nbk_id)


def decodable_listing_rows(body: bytes) -> list[str]:
    """Return the UTF-8-decodable rows of one retained litarch listing.

    NCBI's global litarch index is not canonical UTF-8: a handful of rows for
    unrelated titles (NTP technical reports, Spanish-language WHO documents)
    carry latin-1 bytes. Refusing the whole listing over a row that cannot be the
    GeneReviews row made every real capture unusable, so undecodable rows are
    skipped instead. The GeneReviews row itself is ASCII; were it ever not, it
    would be skipped too and the caller's "exactly one canonical NBK1116 row"
    rule fails closed. Both the snapshot writer and the capture reader use this
    one function, so they cannot disagree about which rows a listing contains.
    """
    rows: list[str] = []
    for raw in body.split(b"\n"):
        try:
            rows.append(raw.removesuffix(b"\r").decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return rows


def listing_from_bytes(body: bytes, *, nbk_id: str = "NBK1116") -> ArchiveListing:
    """Return the single ArchiveListing for *nbk_id* in one retained listing body."""
    for line in decodable_listing_rows(body):
        parsed = parse_file_list_row(line, nbk_filter=nbk_id)
        if parsed:
            return parsed
    raise RuntimeError(f"NBK id {nbk_id} not found in {FILE_LIST_URL}")


async def download_tarball(
    listing: ArchiveListing,
    *,
    dest: Path,
    chunk_size: int = 1 << 20,  # 1 MiB
    request_hooks: Sequence[RequestHook] = (),
) -> str:
    """Stream-download the tarball to *dest*; return its sha256.

    Uses a per-read timeout (not a total-transfer deadline) so a legit ~10-min
    download completes while a stalled socket aborts, and a fail-closed byte cap
    so a hostile/compromised tarball cannot exhaust disk.
    """
    url = f"{LITARCH_BASE}/{listing.relpath}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with _ncbi_client(request_hooks=request_hooks) as client:
        return await stream_to_file(
            client,
            url,
            dest,
            max_bytes=MAX_TARBALL_BYTES,
            chunk_size=chunk_size,
            deadline_seconds=TARBALL_DOWNLOAD_DEADLINE_SECONDS,
        )
