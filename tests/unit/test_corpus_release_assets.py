"""Adversarial tests for release asset URL and credential boundaries."""

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
    async def fake_release_assets(repo: str, tag: str, token: str) -> dict[str, str]:
        return {name: "https://evil.example/" + name for name in release_assets.ASSET_NAMES}

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
