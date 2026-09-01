"""Durable handoff locator identity tests."""

from __future__ import annotations

import json

import pytest

from genereview_link.corpus.handoff_locator import (
    HandoffLocatorError,
    load_handoff_locator,
)


def _locator() -> dict[str, object]:
    names = (
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "seal-manifest.json",
        "genereviews_link-5.1.4-py3-none-any.whl",
    )
    return {
        "format": "genereviews-handoff-locator-v1",
        "object_id": "1" * 64,
        "build_revision": "2" * 40,
        "assets": [
            {
                "name": name,
                "url": f"https://api.github.com/repos/owner/seals/releases/assets/{index}",
                "sha256": f"{index:x}" * 64,
                "size_bytes": 1024,
            }
            for index, name in enumerate(names, 1)
        ],
    }


def test_handoff_locator_binds_exact_sealed_object_and_wheel() -> None:
    locator = _locator()

    loaded = load_handoff_locator(
        (json.dumps(locator) + "\n").encode(),
        allowed_repositories={"owner/seals"},
        expected_object_id="1" * 64,
    )

    assert loaded["build_revision"] == "2" * 40


@pytest.mark.parametrize("field", ["object_id", "build_revision"])
def test_handoff_locator_rejects_untrusted_identity(field: str) -> None:
    locator = _locator()
    locator[field] = "3" * (64 if field == "object_id" else 40)

    with pytest.raises(HandoffLocatorError, match=field.replace("_", " ")):
        load_handoff_locator(
            json.dumps(locator).encode(),
            allowed_repositories={"owner/seals"},
            expected_object_id="1" * 64,
            expected_build_revision="2" * 40,
        )


def test_handoff_locator_rejects_missing_or_non_api_assets() -> None:
    locator = _locator()
    assets = locator["assets"]
    assert isinstance(assets, list)
    assets.pop()
    with pytest.raises(HandoffLocatorError, match="asset set"):
        load_handoff_locator(
            json.dumps(locator).encode(),
            allowed_repositories={"owner/seals"},
            expected_object_id="1" * 64,
        )

    locator = _locator()
    assets = locator["assets"]
    assert isinstance(assets, list)
    asset = assets[0]
    assert isinstance(asset, dict)
    asset["url"] = "https://example.org/corpus.dump"
    with pytest.raises(HandoffLocatorError, match="allowlisted"):
        load_handoff_locator(
            json.dumps(locator).encode(),
            allowed_repositories={"owner/seals"},
            expected_object_id="1" * 64,
        )
