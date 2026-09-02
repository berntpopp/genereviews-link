"""Assemble one retained offline GeneReviews source set directly from NCBI.

``ingest`` deliberately has no live-fetch path: it consumes only bytes that are
already on disk, so that what a release attests is exactly what a maintainer
retained. That contract needs an *acquisition* step in front of it, and this is
it. ``snapshot`` fetches the upstream listing, the GeneReviews litarch archive
and the three side-data files, records their exact identity, and writes the
``source-capture.json``/``file_list.csv`` pair in the precise layout
``genereview-link ingest`` consumes -- and nothing else. It never touches a
database, never embeds, and never publishes.

Rights: acquisition is not redistribution, and the rights gate in ``rights.py``
is unchanged and still governs publication. GeneReviews content is copyrighted
and licensed for noncommercial research use only, so this command still refuses
to write a byte until the operator acknowledges those terms explicitly
(``--acknowledge-terms``), and it records that acknowledgement in the snapshot
manifest next to the bytes it fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

import httpx

from genereview_link.corpus.archive import (
    FILE_LIST_URL,
    LITARCH_BASE,
    MAX_LISTING_BYTES,
    ArchiveListing,
    download_tarball,
    fetch_listing_bytes,
    listing_from_bytes,
)
from genereview_link.corpus.rights_notice import load_rights_notice
from genereview_link.corpus.source_assets import GENESIS_SOURCE_ASSETS, SOURCE_ASSETS
from genereview_link.corpus.source_capture import (
    SourceCaptureError,
    archive_content_identities,
    load_offline_capture,
)
from genereview_link.corpus.source_identity import SIDEDATA_FILES
from genereview_link.strict_json import StrictJsonError, load_strict_json

ARCHIVE_NAME = "gene_NBK1116.tar.gz"
LISTING_NAME = "file_list.csv"
CAPTURE_NAME = "source-capture.json"
SNAPSHOT_MANIFEST_NAME = "snapshot-manifest.json"
SNAPSHOT_FORMAT = "genereviews-source-snapshot-v1"
CAPTURE_FORMAT = "genereviews-offline-source-v1"
SIDEDATA_BASE_URL = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
MAX_CONTROL_BYTES = 4 * 1024 * 1024
# NCBI's published courtesy limits for its public endpoints. The bulk FTP paths
# this command uses are not E-utilities and an API key does not authenticate
# them, but the same politeness floor is the right default either way.
RATE_LIMITED_INTERVAL_SECONDS = 0.34
API_KEY_INTERVAL_SECONDS = 0.11
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceFetchError(ValueError):
    """The upstream snapshot could not be assembled exactly."""


class _Sleeper(Protocol):
    async def __call__(self, delay: float, /) -> object: ...


class PoliteRateLimiter:
    """Enforce a minimum interval between outbound upstream requests.

    Installed as an httpx request event hook, so it also paces redirects and any
    hop a caller did not write by hand. ``sleep``/``clock`` are injectable so a
    test can assert the pacing without spending the wall time.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        sleep: _Sleeper | None = None,
        clock: object = monotonic,
    ) -> None:
        if min_interval < 0:
            raise SourceFetchError("rate-limit interval must not be negative")
        self.min_interval = float(min_interval)
        self._sleep: _Sleeper = sleep if sleep is not None else asyncio.sleep
        self._clock = clock
        self._last: float | None = None
        self.waits: list[float] = []

    async def wait(self) -> None:
        now = float(self._clock())  # type: ignore[operator]
        if self._last is not None:
            delay = self.min_interval - (now - self._last)
            if delay > 0:
                self.waits.append(delay)
                await self._sleep(delay)
                now = float(self._clock())  # type: ignore[operator]
        self._last = now

    async def hook(self, request: httpx.Request) -> None:
        del request
        await self.wait()


def default_min_interval(api_key: str | None) -> float:
    """Pick the courtesy interval NCBI publishes for keyed/unkeyed clients."""
    return API_KEY_INTERVAL_SECONDS if api_key else RATE_LIMITED_INTERVAL_SECONDS


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """What one ``snapshot`` invocation produced, and what it reused."""

    destination: Path
    source_metadata: Path
    archive: Path
    manifest: Path
    listing: ArchiveListing
    chapter_ids: tuple[str, ...]
    genesis: bool
    fetched: tuple[str, ...]
    reused: tuple[str, ...]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_exact(path: Path, content: bytes) -> None:
    """Replace one snapshot file atomically, owner-readable only."""
    temporary = path.with_name(f".{path.name}.partial")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = load_strict_json(path.read_bytes(), max_bytes=MAX_CONTROL_BYTES)
    except (StrictJsonError, OSError):
        return {}
    if not isinstance(value, dict) or value.get("format") != SNAPSHOT_FORMAT:
        return {}
    return value


def _recorded(manifest: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        return None
    entry = files.get(name)
    return entry if isinstance(entry, Mapping) else None


def _is_reusable(path: Path, entry: Mapping[str, object] | None) -> bool:
    """A retained file is reusable only when its recorded digest still holds."""
    if entry is None or not path.is_file() or path.is_symlink():
        return False
    recorded_digest = entry.get("sha256")
    if not isinstance(recorded_digest, str) or not _SHA256.fullmatch(recorded_digest):
        return False
    digest, size = _digest(path)
    return digest == recorded_digest and size == entry.get("size_bytes")


def prior_artifact_from(prior_manifest: Path) -> dict[str, object]:
    """Derive the exact prior-artifact claim from the previous release's manifest.

    Every field is read out of the prior bytes rather than supplied by hand, so
    the capture cannot claim a prior identity the retained file does not prove;
    ``load_offline_capture`` re-derives and re-checks all of it anyway.
    """
    manifest_bytes = prior_manifest.read_bytes()
    try:
        manifest = load_strict_json(manifest_bytes, max_bytes=MAX_CONTROL_BYTES)
    except StrictJsonError as error:
        raise SourceFetchError("prior manifest is not valid JSON") from error
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != "3":
        raise SourceFetchError("prior manifest is not a manifest-v3 corpus release")
    content = manifest.get("content_identity")
    if not isinstance(content, Mapping):
        raise SourceFetchError("prior manifest lacks a logical content identity")
    logical = (
        "chapter_ids",
        "chapter_count",
        "chapter_digests",
        "chapters_sha256",
        "passages_sha256",
    )
    if any(key not in content for key in logical):
        raise SourceFetchError("prior manifest content identity is incomplete")
    return {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "corpus_release_id": manifest.get("corpus_release_id"),
        "app_git_sha": manifest.get("app_git_sha"),
        **{key: content[key] for key in logical},
    }


def _terms_acknowledgement() -> dict[str, object]:
    """Record the committed, reviewed rights notice beside the fetched bytes."""
    notice = load_rights_notice()
    return {
        "acknowledged": True,
        "terms_source_uri": notice.terms_url,
        "terms_version": notice.terms_reviewed_at,
        "permitted_asset_use": notice.use_restriction,
        "attribution": notice.attribution,
    }


def _prepare_destination(destination: Path) -> None:
    if destination.is_symlink():
        raise SourceFetchError("snapshot destination must not be a symlink")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not destination.is_dir():
        raise SourceFetchError("snapshot destination must be a real directory")


async def fetch_source_snapshot(
    destination: Path,
    *,
    genesis: bool,
    acknowledge_terms: bool,
    prior_manifest: Path | None = None,
    rate_limiter: PoliteRateLimiter | None = None,
    api_key: str | None = None,
    refresh: bool = False,
    verify: bool = True,
    nbk_id: str = "NBK1116",
) -> SnapshotResult:
    """Fetch one complete offline source set into *destination*.

    Idempotent by construction: the tiny listing is always refetched because it
    is the upstream freshness oracle, and the ~600 MB archive and side data are
    re-downloaded only when the listing moved, a recorded digest no longer holds,
    or ``refresh`` is set. Re-running after an interruption therefore resumes
    rather than restarting.
    """
    if not acknowledge_terms:
        raise SourceFetchError(
            "refusing to fetch GeneReviews source bytes without an explicit terms "
            "acknowledgement: the content is copyrighted and licensed for "
            "noncommercial research use only"
        )
    if genesis and prior_manifest is not None:
        raise SourceFetchError("a genesis snapshot must not be given a prior manifest")
    if not genesis and prior_manifest is None:
        raise SourceFetchError("a chained snapshot requires the prior release's manifest")

    _prepare_destination(destination)
    limiter = rate_limiter or PoliteRateLimiter(default_min_interval(api_key))
    hooks: Sequence[object] = (limiter.hook,)
    previous = _load_manifest(destination / SNAPSHOT_MANIFEST_NAME)
    fetched: list[str] = []
    reused: list[str] = []

    listing_bytes = await fetch_listing_bytes(request_hooks=hooks)  # type: ignore[arg-type]
    if not 0 < len(listing_bytes) <= MAX_LISTING_BYTES:
        raise SourceFetchError("upstream listing is empty or beyond its reviewed bound")
    listing_captured_at = _now()
    listing = listing_from_bytes(listing_bytes, nbk_id=nbk_id)
    _write_exact(destination / LISTING_NAME, listing_bytes)
    fetched.append(LISTING_NAME)

    moved = _recorded(previous, ARCHIVE_NAME) is None or any(
        (_recorded(previous, ARCHIVE_NAME) or {}).get(key) != value
        for key, value in (("relpath", listing.relpath), ("last_updated", listing.last_updated))
    )
    archive_path = destination / ARCHIVE_NAME
    archive_url = f"{LITARCH_BASE}/{listing.relpath}"
    if refresh or moved or not _is_reusable(archive_path, _recorded(previous, ARCHIVE_NAME)):
        archive_path.unlink(missing_ok=True)
        await download_tarball(listing, dest=archive_path, request_hooks=hooks)  # type: ignore[arg-type]
        fetched.append(ARCHIVE_NAME)
    else:
        reused.append(ARCHIVE_NAME)
    archive_sha256, archive_size = _digest(archive_path)

    side_stale = (
        refresh
        or moved
        or any(
            not _is_reusable(destination / name, _recorded(previous, name))
            for name in SIDEDATA_FILES
        )
    )
    if side_stale:
        for name in SIDEDATA_FILES:
            (destination / name).unlink(missing_ok=True)
        from genereview_link.corpus.pipeline import _download_sidedata

        await _download_sidedata(destination, request_hooks=hooks)  # type: ignore[arg-type]
        fetched.extend(SIDEDATA_FILES)
    else:
        reused.extend(SIDEDATA_FILES)
    side_identity = {name: _digest(destination / name) for name in SIDEDATA_FILES}

    cached = previous.get("archive_content_identities")
    if (
        isinstance(cached, Mapping)
        and cached.get("sha256") == archive_sha256
        and _SHA256.fullmatch(str(cached.get("members_sha256", "")))
        and _SHA256.fullmatch(str(cached.get("expanded_sha256", "")))
    ):
        members_sha256 = str(cached["members_sha256"])
        expanded_sha256 = str(cached["expanded_sha256"])
    else:
        members_sha256, expanded_sha256 = archive_content_identities(archive_path)

    from genereview_link.corpus.sidedata import load_sidedata

    chapter_ids = sorted(load_sidedata(destination).short_name_by_nbk)
    if not chapter_ids:
        raise SourceFetchError("upstream side data named no GeneReviews chapters")

    prior: dict[str, object] | None = None
    if not genesis:
        assert prior_manifest is not None
        _write_exact(destination / "prior-manifest.json", prior_manifest.read_bytes())
        prior = prior_artifact_from(destination / "prior-manifest.json")

    capture: dict[str, object] = {
        "format": CAPTURE_FORMAT,
        "listing": {
            "url": FILE_LIST_URL,
            "raw_sha256": hashlib.sha256(listing_bytes).hexdigest(),
            "raw_size_bytes": len(listing_bytes),
            "captured_at": listing_captured_at,
            "integrity_class": "https-captured-untrusted",
            "relpath": listing.relpath,
            "last_updated": listing.last_updated,
        },
        "archive": {
            "url": archive_url,
            "sha256": archive_sha256,
            "size_bytes": archive_size,
            "members_sha256": members_sha256,
            "expanded_sha256": expanded_sha256,
        },
        "side_data": {
            name: {
                "url": f"{SIDEDATA_BASE_URL}/{name}",
                "sha256": side_identity[name][0],
                "size_bytes": side_identity[name][1],
            }
            for name in SIDEDATA_FILES
        },
        "chapter_ids": chapter_ids,
        "prior_artifact": prior,
    }
    if genesis:
        capture["genesis"] = True
    _write_exact(destination / CAPTURE_NAME, _canonical(capture))

    expected = GENESIS_SOURCE_ASSETS if genesis else SOURCE_ASSETS
    missing = sorted(name for name in expected if not (destination / name).is_file())
    if missing:
        raise SourceFetchError(f"snapshot is missing required source files: {', '.join(missing)}")

    manifest = {
        "format": SNAPSHOT_FORMAT,
        "captured_at": listing_captured_at,
        "genesis": genesis,
        "nbk_id": nbk_id,
        "chapter_count": len(chapter_ids),
        "terms": _terms_acknowledgement(),
        "archive_content_identities": {
            "sha256": archive_sha256,
            "members_sha256": members_sha256,
            "expanded_sha256": expanded_sha256,
        },
        "files": {
            LISTING_NAME: {
                "url": FILE_LIST_URL,
                "sha256": hashlib.sha256(listing_bytes).hexdigest(),
                "size_bytes": len(listing_bytes),
                "captured_at": listing_captured_at,
            },
            ARCHIVE_NAME: {
                "url": archive_url,
                "sha256": archive_sha256,
                "size_bytes": archive_size,
                "relpath": listing.relpath,
                "last_updated": listing.last_updated,
                "captured_at": listing_captured_at,
            },
            **{
                name: {
                    "url": f"{SIDEDATA_BASE_URL}/{name}",
                    "sha256": side_identity[name][0],
                    "size_bytes": side_identity[name][1],
                    "captured_at": listing_captured_at,
                }
                for name in SIDEDATA_FILES
            },
            CAPTURE_NAME: {
                "sha256": hashlib.sha256(_canonical(capture)).hexdigest(),
                "size_bytes": len(_canonical(capture)),
                "captured_at": listing_captured_at,
            },
        },
    }
    _write_exact(destination / SNAPSHOT_MANIFEST_NAME, _canonical(manifest))

    if verify:
        try:
            loaded = load_offline_capture(
                destination / CAPTURE_NAME,
                archive=archive_path,
                side_data_dir=destination,
                prior_manifest=None if genesis else destination / "prior-manifest.json",
            )
        except SourceCaptureError as error:
            raise SourceFetchError(f"assembled snapshot is not ingestable: {error}") from error
        if loaded != capture:
            raise SourceFetchError("assembled snapshot does not round-trip through ingest's reader")

    return SnapshotResult(
        destination=destination,
        source_metadata=destination / CAPTURE_NAME,
        archive=archive_path,
        manifest=destination / SNAPSHOT_MANIFEST_NAME,
        listing=listing,
        chapter_ids=tuple(chapter_ids),
        genesis=genesis,
        fetched=tuple(fetched),
        reused=tuple(reused),
    )


__all__ = [
    "PoliteRateLimiter",
    "SnapshotResult",
    "SourceFetchError",
    "default_min_interval",
    "fetch_source_snapshot",
    "prior_artifact_from",
]
