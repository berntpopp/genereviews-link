"""Outbound-download safety primitives: redirect allowlisting + byte ceilings.

Shared by the NCBI corpus ingest path and the GitHub release-bundle download
path. Both stream to disk (or bounded memory) with a running SHA-256 and a
fail-closed byte cap, and pin every request hop -- including auto-followed
redirects -- to an exact host allowlist.

The guard/cap exceptions deliberately subclass plain ``Exception`` (NOT
``httpx.TransportError``/``TimeoutException``) so a caller's retry loop treats
them as non-retryable.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx

# Per-read deadlines protect a stalled socket.  The independent end-to-end
# deadline ensures a peer cannot keep a transfer alive forever by dripping
# bytes just inside that read timeout.  Twenty minutes leaves ample headroom
# for the ~600 MB corpus on a slow link.
STREAM_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
MAX_DOWNLOAD_SECONDS = 20 * 60.0

RequestHook = Callable[[httpx.Request], Awaitable[None]]


class DisallowedURLError(Exception):
    """Outbound request/redirect targets a non-allowlisted URL. NON-RETRYABLE."""


class ResponseTooLargeError(Exception):
    """A streamed download exceeded its byte ceiling. NON-RETRYABLE."""


class DownloadDeadlineError(Exception):
    """A download exceeded its monotonic end-to-end deadline. NON-RETRYABLE."""


def build_host_allowlist(*urls: str) -> frozenset[str]:
    """Collect the lowercased hostnames of *urls* into an exact allowlist."""
    hosts: set[str] = set()
    for url in urls:
        host = urlsplit(url).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def make_url_guard(allowed_hosts: frozenset[str]) -> RequestHook:
    """Return an httpx request event-hook that validates every hop.

    Fires on the initial request and on each auto-followed redirect. Rejects
    non-https schemes, embedded userinfo, and any host outside *allowed_hosts*
    (exact match -- no suffix/substring).
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(f"non-https scheme: {url.scheme}")
        if url.username or url.password:
            raise DisallowedURLError("userinfo not permitted in URL")
        host = (url.host or "").lower()
        if host not in allowed_hosts:
            raise DisallowedURLError(f"host not allowlisted: {host}")

    return _guard


def _reject_declared_length(resp: httpx.Response, max_bytes: int) -> None:
    """Cheap first guard: reject an oversized declared Content-Length.

    Not trusted alone (chunked/gzip omit or understate it); the streaming loop
    is the authoritative cap.
    """
    raw = resp.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > max_bytes:
        raise ResponseTooLargeError(f"declared content-length {declared} exceeds {max_bytes} bytes")


def _reject_expired_deadline(deadline_at: float) -> None:
    if monotonic() >= deadline_at:
        raise DownloadDeadlineError("download exceeded end-to-end deadline")


async def stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    chunk_size: int = 1 << 20,
    deadline_seconds: float = MAX_DOWNLOAD_SECONDS,
) -> str:
    """Stream *url* to *dest* under byte and monotonic time caps; return SHA-256.

    On cap overflow the partial file is removed and ``ResponseTooLargeError`` is
    raised.  A separate end-to-end deadline applies even when every individual
    read completes before ``STREAM_TIMEOUT.read``.
    """
    sha = hashlib.sha256()
    total = 0
    deadline_at = monotonic() + deadline_seconds
    try:
        async with asyncio.timeout(deadline_seconds):
            async with client.stream("GET", url) as resp:
                _reject_expired_deadline(deadline_at)
                resp.raise_for_status()
                _reject_declared_length(resp, max_bytes)
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        _reject_expired_deadline(deadline_at)
                        total += len(chunk)
                        if total > max_bytes:
                            raise ResponseTooLargeError(f"download exceeded {max_bytes} bytes")
                        sha.update(chunk)
                        fh.write(chunk)
        return sha.hexdigest()
    except TimeoutError as exc:
        raise DownloadDeadlineError("download exceeded end-to-end deadline") from exc
    except (DownloadDeadlineError, ResponseTooLargeError):
        raise
    finally:
        if total > max_bytes or monotonic() >= deadline_at:
            dest.unlink(missing_ok=True)


async def read_capped(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    chunk_size: int = 1 << 16,
    deadline_seconds: float = MAX_DOWNLOAD_SECONDS,
) -> bytes:
    """GET *url* into memory under byte and monotonic time caps."""
    chunks: list[bytes] = []
    total = 0
    deadline_at = monotonic() + deadline_seconds
    try:
        async with asyncio.timeout(deadline_seconds):
            async with client.stream("GET", url) as resp:
                _reject_expired_deadline(deadline_at)
                resp.raise_for_status()
                _reject_declared_length(resp, max_bytes)
                async for chunk in resp.aiter_bytes(chunk_size):
                    _reject_expired_deadline(deadline_at)
                    total += len(chunk)
                    if total > max_bytes:
                        raise ResponseTooLargeError(f"response exceeded {max_bytes} bytes")
                    chunks.append(chunk)
        return b"".join(chunks)
    except TimeoutError as exc:
        raise DownloadDeadlineError("download exceeded end-to-end deadline") from exc
