"""Adversarial tests for release asset URL and credential boundaries."""

import json
from pathlib import Path

import httpx
import pytest
import respx

import genereview_link.corpus.release_assets as release_assets
from genereview_link.download_guard import DisallowedURLError


@pytest.mark.asyncio
@respx.mock
async def test_release_controlled_asset_host_cannot_expand_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_release_assets(
        repo: str,
        tag: str,
        token: str,
        *,
        release_id: int | None = None,
        allow_draft: bool = False,
    ) -> release_assets.ReleaseIdentity:
        del repo, token, release_id, allow_draft
        return release_assets.ReleaseIdentity(
            release_id=7,
            tag=tag,
            target_commit="a" * 40,
            assets=tuple(
                release_assets.AssetIdentity(
                    asset_id=index,
                    name=name,
                    size=8,
                    digest="sha256:" + "b" * 64,
                    url="https://evil.example/" + name,
                )
                for index, name in enumerate(release_assets.PUBLICATION_ASSET_NAMES, start=1)
            ),
        )

    route = respx.get("https://evil.example/manifest.json").mock(
        return_value=httpx.Response(200, content=b"attacker")
    )
    monkeypatch.setattr(release_assets, "_release_assets", fake_release_assets)
    destination = tmp_path / "fresh"
    destination.mkdir()

    with pytest.raises(DisallowedURLError, match="host not allowlisted"):
        await release_assets.download_release_assets(
            "owner/repo", "tag", destination, token="s" * 8
        )

    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_downloader_returns_release_and_downloaded_byte_identity(tmp_path: Path) -> None:
    target = "a" * 40
    bodies = {
        "manifest.json": b"{}\n",
        "SHA256SUMS": b"x\n",
        "corpus.dump": b"PGDMP-data",
        "rights-record.json": b'{"decision":"affirmative"}\n',
        "rights-evidence.json": b'{"reviewed":true}\n',
        "terms-snapshot.html": b"<html>terms</html>\n",
        "seal-manifest.json": b'{"format":"genereviews-local-handoff-v1"}\n',
        "publisher-tool.whl": b"PK\x03\x04sealed publisher wheel",
    }
    assets = []
    for index, (name, body) in enumerate(bodies.items(), start=1):
        digest = release_assets.hashlib.sha256(body).hexdigest()
        assets.append(
            {
                "id": index,
                "name": name,
                "size": len(body),
                "digest": f"sha256:{digest}",
                "url": f"https://api.github.com/repos/o/r/releases/assets/{index}",
            }
        )
        respx.get(f"https://api.github.com/repos/o/r/releases/assets/{index}").mock(
            return_value=httpx.Response(200, content=body)
        )
    respx.get("https://api.github.com/repos/o/r/releases/tags/t").mock(
        return_value=httpx.Response(
            200,
            content=json.dumps(
                {
                    "id": 99,
                    "tag_name": "t",
                    "target_commitish": target,
                    "draft": False,
                    "prerelease": False,
                    "immutable": True,
                    "assets": assets,
                }
            ).encode(),
        )
    )
    destination = tmp_path / "fresh"
    destination.mkdir()

    identity = await release_assets.download_release_assets("o/r", "t", destination)

    assert identity.release_id == 99
    assert identity.target_commit == target
    assert {asset.name: asset.downloaded_sha256 for asset in identity.assets} == {
        name: release_assets.hashlib.sha256(body).hexdigest() for name, body in bodies.items()
    }
    assert (destination / "rights-record.json").is_file()
