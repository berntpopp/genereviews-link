"""Adversarial tests (F-06): the GitHub-Release bundle download must validate
every redirect hop against an exact host allowlist, bound its size, and anchor
authenticity in a COMMITTED digest (not the same-host .sha256). All httpx I/O is
mocked -- no real ~600 MB bundle is fetched.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from genereview_link.config import settings
from genereview_link.download_guard import DisallowedURLError, ResponseTooLargeError
from genereview_link.ingest import github_release as gh

BUNDLE_URL = "https://github.com/berntpopp/genereviews-link/releases/download/v1/bundle.tar.gz"
CDN_URL = "https://release-assets.githubusercontent.com/12345/bundle.tar.gz"


# ---- URL guard (unit, deterministic) ---------------------------------------


async def test_url_guard_rejects_scheme_userinfo_and_host() -> None:
    from genereview_link.download_guard import make_url_guard

    guard = make_url_guard(frozenset({"github.com"}))
    await guard(httpx.Request("GET", "https://github.com/ok"))  # allowed
    for bad in (
        "http://github.com/x",  # non-https downgrade
        "https://user:pass@github.com/x",  # userinfo
        "https://evil.example/x",  # host not allowlisted
    ):
        with pytest.raises(DisallowedURLError):
            await guard(httpx.Request("GET", bad))


# ---- download redirect allowlist -------------------------------------------


@respx.mock(assert_all_called=False)
async def test_download_follows_github_to_cdn_hop(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"corpus-bundle-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    # Anchor authenticity so the (separate) redirect-hop behaviour under test is
    # not masked by the fail-closed unanchored-promotion guard.
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", digest)
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(302, headers={"Location": CDN_URL}))
    respx_mock.get(CDN_URL).mock(return_value=httpx.Response(200, content=payload))

    dest = tmp_path / "bundle.tar.gz"
    await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256=digest)
    assert dest.read_bytes() == payload
    assert not (tmp_path / "bundle.tar.gz.part").exists()


@respx.mock(assert_all_called=False)
async def test_download_rejects_redirect_to_disallowed_host(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    respx_mock.get(BUNDLE_URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with pytest.raises(DisallowedURLError):
        await gh.download_with_integrity(
            BUNDLE_URL, tmp_path / "b.tar.gz", expected_sha256="0" * 64
        )


@respx.mock(assert_all_called=False)
async def test_download_rejects_non_https_redirect(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    respx_mock.get(BUNDLE_URL).mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://release-assets.githubusercontent.com/x"}
        )
    )
    with pytest.raises(DisallowedURLError):
        await gh.download_with_integrity(
            BUNDLE_URL, tmp_path / "b.tar.gz", expected_sha256="0" * 64
        )


# ---- size cap --------------------------------------------------------------


@respx.mock(assert_all_called=False)
async def test_download_enforces_byte_ceiling(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gh, "MAX_BUNDLE_BYTES", 8)
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(200, content=b"x" * 4096))
    dest = tmp_path / "bundle.tar.gz"
    with pytest.raises(ResponseTooLargeError):
        await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256="0" * 64)
    assert not dest.exists()
    assert not (tmp_path / "bundle.tar.gz.part").exists()


# ---- authenticity anchor ---------------------------------------------------


@respx.mock(assert_all_called=False)
async def test_download_fails_closed_on_committed_digest_mismatch(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"tampered-bundle"
    sibling_sha = hashlib.sha256(payload).hexdigest()  # same-host integrity: matches
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "a" * 64)  # committed anchor: differs
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(200, content=payload))

    dest = tmp_path / "bundle.tar.gz"
    with pytest.raises(RuntimeError, match="authenticity"):
        await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256=sibling_sha)
    assert not dest.exists()
    assert not (tmp_path / "bundle.tar.gz.part").exists()


@respx.mock(assert_all_called=False)
async def test_download_passes_matching_committed_digest(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"authentic-bundle"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", digest)
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(200, content=payload))

    dest = tmp_path / "bundle.tar.gz"
    await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256=digest)
    assert dest.read_bytes() == payload


# ---- fail-closed when unanchored (F-06 gate) -------------------------------


@respx.mock(assert_all_called=False)
async def test_download_fails_closed_without_committed_anchor(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No independent anchor + no explicit opt-in => refuse promotion.

    A same-host `.sha256` (expected_sha256) proves transport integrity but NOT
    authenticity: a host that can serve a tampered bundle can serve a matching
    sibling too. With EXPECTED_BUNDLE_SHA256 unset, no in-repo anchor, and
    ALLOW_UNANCHORED_BUNDLE=false, the download must fail closed and leave no
    promoted file behind.
    """
    payload = b"same-host-authenticated-only"
    sibling_sha = hashlib.sha256(payload).hexdigest()  # transport integrity matches
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "")
    monkeypatch.setattr(gh, "BUNDLE_DIGEST_ANCHORS", {})
    monkeypatch.setattr(settings, "ALLOW_UNANCHORED_BUNDLE", False)
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(200, content=payload))

    dest = tmp_path / "bundle.tar.gz"
    with pytest.raises(RuntimeError, match="anchor"):
        await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256=sibling_sha)
    assert not dest.exists()
    assert not (tmp_path / "bundle.tar.gz.part").exists()


@respx.mock(assert_all_called=False)
async def test_download_unanchored_opt_in_allows_promotion(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit ALLOW_UNANCHORED_BUNDLE=true escape hatch restores transport-
    integrity-only promotion for operators who accept that risk knowingly."""
    payload = b"unanchored-but-opted-in"
    sibling_sha = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "")
    monkeypatch.setattr(gh, "BUNDLE_DIGEST_ANCHORS", {})
    monkeypatch.setattr(settings, "ALLOW_UNANCHORED_BUNDLE", True)
    respx_mock.get(BUNDLE_URL).mock(return_value=httpx.Response(200, content=payload))

    dest = tmp_path / "bundle.tar.gz"
    await gh.download_with_integrity(BUNDLE_URL, dest, expected_sha256=sibling_sha)
    assert dest.read_bytes() == payload


def test_committed_digest_prefers_config_then_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "")
    monkeypatch.setattr(gh, "BUNDLE_DIGEST_ANCHORS", {"bundle.tar.gz": "BEEF" + "0" * 60})
    assert gh.committed_bundle_digest(BUNDLE_URL) == ("beef" + "0" * 60)
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "F" * 64)
    assert gh.committed_bundle_digest(BUNDLE_URL) == "f" * 64
    monkeypatch.setattr(settings, "EXPECTED_BUNDLE_SHA256", "")
    monkeypatch.setattr(gh, "BUNDLE_DIGEST_ANCHORS", {})
    assert gh.committed_bundle_digest(BUNDLE_URL) is None


# ---- resolve client host pin -----------------------------------------------


@respx.mock(assert_all_called=False)
async def test_resolve_latest_returns_selected_asset(respx_mock: respx.Router) -> None:
    respx_mock.get("https://api.github.com/repos/owner/repo/releases/latest").mock(
        return_value=httpx.Response(
            200,
            json={
                "assets": [
                    {
                        "name": "genereview-corpus-2026-05-12-r1.tar.gz",
                        "browser_download_url": CDN_URL,
                    }
                ]
            },
        )
    )
    assert await gh.resolve_latest("owner/repo") == CDN_URL
