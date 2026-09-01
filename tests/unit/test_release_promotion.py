"""Mutation-at-each-gap tests for the release promotion state machine."""

from __future__ import annotations

from copy import deepcopy

import pytest

from genereview_link.corpus.release_promotion import (
    PromotionStateError,
    assert_postpublication,
    assert_prepatch,
    freeze_release,
)


def _release(*, draft: bool = True) -> dict[str, object]:
    names = [
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "rights-record.json",
        "rights-evidence.json",
        "terms-snapshot.html",
        "seal-manifest.json",
        "publisher-tool.whl",
    ]
    return {
        "id": 17,
        "tag_name": "corpus-data-2026-08-30-r1",
        "target_commitish": "a" * 40,
        "draft": draft,
        "immutable": not draft,
        "assets": [
            {"id": index, "name": name, "size": index, "digest": "sha256:" + str(index) * 64}
            for index, name in enumerate(names, start=1)
        ],
    }


def _frozen() -> dict[str, object]:
    return freeze_release(
        _release(),
        etag='"verified"',
        tag="corpus-data-2026-08-30-r1",
        target_commit="a" * 40,
    )


@pytest.mark.parametrize("status", [200, 412])
def test_prepatch_rejects_mutation_after_semantic_verification(status: int) -> None:
    with pytest.raises(PromotionStateError):
        assert_prepatch(_frozen(), conditional_status=status)


@pytest.mark.parametrize("mutation", ["asset_id", "asset_digest", "target", "release_id"])
def test_postpublication_rejects_mutation_at_every_release_gap(mutation: str) -> None:
    published = _release(draft=False)
    if mutation == "asset_id":
        published["assets"][0]["id"] = 999  # type: ignore[index]
    elif mutation == "asset_digest":
        published["assets"][0]["digest"] = "sha256:" + "f" * 64  # type: ignore[index]
    elif mutation == "target":
        published["target_commitish"] = "b" * 40
    else:
        published["id"] = 18

    with pytest.raises(PromotionStateError):
        assert_postpublication(
            _frozen(),
            published=deepcopy(published),
            tag_ref={"object": {"type": "commit", "sha": "a" * 40}},
        )
