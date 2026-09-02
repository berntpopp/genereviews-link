"""`snapshot`: acquire the offline source set `ingest` consumes, and nothing else.

Every upstream response is mocked -- no real ~600 MB archive is fetched. What
these tests pin is the contract that makes the command useful: the exact output
layout, a manifest of what was fetched, re-runs that resume instead of
re-downloading, a politeness hook on every request, and a hard refusal to touch
copyrighted upstream bytes without an explicit terms acknowledgement.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
import respx

from genereview_link.corpus import source_fetch
from genereview_link.corpus.source_fetch import (
    PoliteRateLimiter,
    SourceFetchError,
    default_min_interval,
    fetch_source_snapshot,
)

LISTING = (
    b"other/thing.tar.gz,Other,NCBI,2001,NBK0000,2020-01-01 00:00:00\n"
    b"ca/84/gene_NBK1116.tar.gz,GeneReviews,NCBI,1993,NBK1116,2026-08-31 02:41:04\n"
)
ARCHIVE_URL = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/ca/84/gene_NBK1116.tar.gz"
LISTING_URL = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv"
SIDEDATA_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
SIDEDATA = {
    "GRtitle_shortname_NBKid.txt": b"nine\tChapter nine\tNBK9999\t1\n",
    "NBKid_shortname_genesymbol.txt": b"NBK9999\tnine\tGENE9\n",
    "NBKid_shortname_OMIM.txt": b"NBK9999\tnine\t100009\n",
}


def _build_archive() -> bytes:
    buffer = io.BytesIO()
    payload = b"<article>NBK9999</article>"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("NBK9999/NBK9999.nxml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# gzip stamps a timestamp, so build the fixture archive exactly once.
ARCHIVE_BYTES = _build_archive()


def _archive_bytes() -> bytes:
    return ARCHIVE_BYTES


def _mock_upstream(router: respx.Router) -> dict[str, respx.Route]:
    routes = {
        "listing": router.get(LISTING_URL).mock(return_value=httpx.Response(200, content=LISTING)),
        "archive": router.get(ARCHIVE_URL).mock(
            return_value=httpx.Response(200, content=_archive_bytes())
        ),
    }
    for name, body in SIDEDATA.items():
        routes[name] = router.get(f"{SIDEDATA_BASE}/{name}").mock(
            return_value=httpx.Response(200, content=body)
        )
    return routes


class _RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float, /) -> None:
        self.delays.append(delay)


class _StepClock:
    """A clock that never advances, so every request owes the full interval."""

    def __call__(self) -> float:
        return 100.0


async def _snapshot(destination: Path, **kwargs: object) -> source_fetch.SnapshotResult:
    defaults: dict[str, object] = {
        "genesis": True,
        "acknowledge_terms": True,
        "rate_limiter": PoliteRateLimiter(0.0),
    }
    defaults.update(kwargs)
    return await fetch_source_snapshot(destination, **defaults)  # type: ignore[arg-type]


@respx.mock
async def test_snapshot_writes_exactly_the_layout_ingest_consumes(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)
    destination = tmp_path / "source"

    result = await _snapshot(destination)

    assert {path.name for path in destination.iterdir()} == {
        "source-capture.json",
        "file_list.csv",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
        "snapshot-manifest.json",
    }
    assert result.chapter_ids == ("NBK9999",)
    assert result.genesis is True
    capture = json.loads(result.source_metadata.read_bytes())
    assert capture["genesis"] is True
    assert capture["prior_artifact"] is None
    assert capture["listing"]["relpath"] == "ca/84/gene_NBK1116.tar.gz"
    assert capture["archive"]["url"] == ARCHIVE_URL
    # verify=True already round-tripped this through ingest's own reader.


@respx.mock
async def test_snapshot_manifest_records_what_was_fetched(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)
    destination = tmp_path / "source"

    result = await _snapshot(destination)

    manifest = json.loads(result.manifest.read_bytes())
    assert manifest["format"] == "genereviews-source-snapshot-v1"
    assert manifest["chapter_count"] == 1
    assert manifest["terms"]["acknowledged"] is True
    assert "noncommercial research purposes only" in manifest["terms"]["permitted_asset_use"]
    entry = manifest["files"]["gene_NBK1116.tar.gz"]
    assert entry["url"] == ARCHIVE_URL
    assert entry["sha256"] == hashlib.sha256(_archive_bytes()).hexdigest()
    assert entry["size_bytes"] == len(_archive_bytes())
    assert entry["last_updated"] == "2026-08-31 02:41:04"
    assert manifest["files"]["file_list.csv"]["sha256"] == hashlib.sha256(LISTING).hexdigest()
    for name, body in SIDEDATA.items():
        assert manifest["files"][name]["sha256"] == hashlib.sha256(body).hexdigest()


@respx.mock
async def test_snapshot_reruns_resume_instead_of_redownloading(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    routes = _mock_upstream(respx_mock)
    destination = tmp_path / "source"

    first = await _snapshot(destination)
    second = await _snapshot(destination)

    assert "gene_NBK1116.tar.gz" in first.fetched
    assert second.fetched == ("file_list.csv",)
    assert set(second.reused) == {
        "gene_NBK1116.tar.gz",
        *SIDEDATA,
    }
    assert routes["archive"].call_count == 1
    assert routes["GRtitle_shortname_NBKid.txt"].call_count == 1
    # the listing is the freshness oracle, so it is always refetched
    assert routes["listing"].call_count == 2


@respx.mock
async def test_snapshot_refetches_when_upstream_moves(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    routes = _mock_upstream(respx_mock)
    destination = tmp_path / "source"
    await _snapshot(destination)

    moved = LISTING.replace(b"2026-08-31 02:41:04", b"2026-09-01 02:41:04")
    routes["listing"].mock(return_value=httpx.Response(200, content=moved))
    result = await _snapshot(destination)

    assert "gene_NBK1116.tar.gz" in result.fetched
    assert routes["archive"].call_count == 2


@respx.mock
async def test_snapshot_forces_a_refresh_on_demand(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    routes = _mock_upstream(respx_mock)
    destination = tmp_path / "source"
    await _snapshot(destination)

    result = await _snapshot(destination, refresh=True)

    assert result.reused == ()
    assert routes["archive"].call_count == 2


@respx.mock
async def test_snapshot_paces_every_upstream_request(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)
    sleeper = _RecordingSleeper()
    limiter = PoliteRateLimiter(0.34, sleep=sleeper, clock=_StepClock())

    await _snapshot(tmp_path / "source", rate_limiter=limiter)

    # five upstream GETs: listing, archive, three side-data files; the first is
    # free and every subsequent one waits out the full interval.
    assert sleeper.delays == pytest.approx([0.34] * 4)


def test_default_interval_follows_ncbis_published_limits() -> None:
    assert default_min_interval(None) == 0.34
    assert default_min_interval("key") == 0.11


@respx.mock
async def test_snapshot_refuses_without_a_terms_acknowledgement(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    routes = _mock_upstream(respx_mock)
    destination = tmp_path / "source"

    with pytest.raises(SourceFetchError, match="noncommercial research use only"):
        await fetch_source_snapshot(destination, genesis=True, acknowledge_terms=False)

    assert not destination.exists()
    assert routes["listing"].call_count == 0


@respx.mock
async def test_genesis_snapshot_refuses_a_prior_pair(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)

    with pytest.raises(SourceFetchError, match="must not be given a prior manifest pair"):
        await _snapshot(tmp_path / "source", prior_manifest=tmp_path / "p")


@respx.mock
async def test_chained_snapshot_requires_both_prior_files(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)

    with pytest.raises(SourceFetchError, match="requires both prior manifest files"):
        await _snapshot(tmp_path / "source", genesis=False, prior_manifest=tmp_path / "p")


@respx.mock
async def test_chained_snapshot_derives_the_prior_from_retained_bytes(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    _mock_upstream(respx_mock)
    content_identity = {
        "chapter_ids": ["NBK9999"],
        "chapter_count": 1,
        "chapter_digests": {"NBK9999": "a" * 64},
        "chapters_sha256": "b" * 64,
        "passages_sha256": "c" * 64,
    }
    manifest_bytes = json.dumps(
        {
            "manifest_version": "3",
            "corpus_release_id": "2026-08-31-r1",
            "app_git_sha": "1" * 40,
            "content_identity": content_identity,
        },
        sort_keys=True,
    ).encode()
    prior_manifest = tmp_path / "prior-manifest.json"
    prior_manifest.write_bytes(manifest_bytes)
    prior_seal = tmp_path / "prior-seal-manifest.json"
    prior_seal.write_bytes(
        (
            json.dumps(
                {
                    "format": "genereviews-local-handoff-v1",
                    "corpus_release_id": "2026-08-31-r1",
                    "genesis": True,
                    "prior": None,
                    "files": [
                        {
                            "name": "manifest.json",
                            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                            "size": len(manifest_bytes),
                            "mode": 0o400,
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )
    destination = tmp_path / "source"

    result = await _snapshot(
        destination,
        genesis=False,
        prior_manifest=prior_manifest,
        prior_seal_manifest=prior_seal,
    )

    capture = json.loads(result.source_metadata.read_bytes())
    assert "genesis" not in capture
    assert capture["prior_artifact"]["corpus_release_id"] == "2026-08-31-r1"
    assert (
        capture["prior_artifact"]["object_id"]
        == hashlib.sha256(prior_seal.read_bytes()).hexdigest()
    )
    assert (destination / "prior-manifest.json").read_bytes() == manifest_bytes


@respx.mock
async def test_snapshot_refuses_a_listing_without_the_canonical_row(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    routes = _mock_upstream(respx_mock)
    routes["listing"].mock(
        return_value=httpx.Response(200, content=b"x/y.tar.gz,Other,NCBI,2001,NBK0000,2020-01-01\n")
    )

    with pytest.raises(RuntimeError, match="NBK1116 not found"):
        await _snapshot(tmp_path / "source")
