"""Draft-resume and annotated-tag publication transaction tests."""

from __future__ import annotations

import pytest

from genereview_link.corpus.release_transaction import (
    ReleaseTransactionError,
    plan_release,
    verify_annotated_tag,
)

EXPECTED = {
    f"asset-{index}": {"size": index, "digest": f"sha256:{index:x}" * 1 + "0" * 63}
    for index in range(1, 9)
}
MARKERS = ("rights-record-sha256: " + "1" * 64, "terms-sha256: " + "2" * 64)


def _release(*, draft: bool, count: int = 8) -> dict[str, object]:
    return {
        "id": 17,
        "tag_name": "corpus-data-2026-08-30-r1",
        "target_commitish": "a" * 40,
        "draft": draft,
        "immutable": not draft,
        "published_at": None if draft else "2026-09-01T12:00:00Z",
        "prerelease": False,
        "body": f"Immutable GeneReviews corpus; {MARKERS[0]}; {MARKERS[1]}",
        "assets": [
            {"id": index, "name": name, **EXPECTED[name]}
            for index, name in enumerate(list(EXPECTED)[:count], 1)
        ],
    }


def test_partial_exact_draft_resumes_only_missing_assets() -> None:
    plan = plan_release(
        _release(draft=True, count=3),
        tag="corpus-data-2026-08-30-r1",
        target="a" * 40,
        expected_assets=EXPECTED,
        required_body_markers=MARKERS,
    )

    assert plan.action == "resume"
    assert plan.missing_assets == tuple(list(EXPECTED)[3:])


def test_absent_release_creates_all_assets() -> None:
    plan = plan_release(
        None,
        tag="corpus-data-2026-08-30-r1",
        target="a" * 40,
        expected_assets=EXPECTED,
        required_body_markers=MARKERS,
    )
    assert plan.action == "create"
    assert plan.missing_assets == tuple(EXPECTED)


def test_partial_draft_with_conflicting_bytes_fails_closed() -> None:
    release = _release(draft=True, count=3)
    assets = release["assets"]
    assert isinstance(assets, list) and isinstance(assets[0], dict)
    assets[0]["digest"] = "sha256:" + "f" * 64

    with pytest.raises(ReleaseTransactionError, match="conflicts"):
        plan_release(
            release,
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            expected_assets=EXPECTED,
            required_body_markers=MARKERS,
        )


def test_complete_draft_promotes_and_exact_immutable_is_noop() -> None:
    assert (
        plan_release(
            _release(draft=True),
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            expected_assets=EXPECTED,
            required_body_markers=MARKERS,
        ).action
        == "promote"
    )
    assert (
        plan_release(
            _release(draft=False),
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            expected_assets=EXPECTED,
            required_body_markers=MARKERS,
        ).action
        == "noop"
    )


def test_partial_draft_with_wrong_rights_body_fails_before_resume() -> None:
    release = _release(draft=True, count=3)
    release["body"] = "unbound draft"

    with pytest.raises(ReleaseTransactionError, match="rights markers"):
        plan_release(
            release,
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            expected_assets=EXPECTED,
            required_body_markers=MARKERS,
        )


def test_wrong_target_fails_before_resume() -> None:
    release = _release(draft=True, count=3)
    release["target_commitish"] = "b" * 40
    with pytest.raises(ReleaseTransactionError, match="identity conflicts"):
        plan_release(
            release,
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            expected_assets=EXPECTED,
            required_body_markers=MARKERS,
        )


def test_only_exact_annotated_tag_can_resume_final_promotion() -> None:
    tag_ref = {"object": {"type": "tag", "sha": "b" * 40}}
    tag_object = {
        "sha": "b" * 40,
        "tag": "corpus-data-2026-08-30-r1",
        "message": "GeneReviews sealed object " + "c" * 64,
        "object": {"type": "commit", "sha": "a" * 40},
    }
    verify_annotated_tag(
        tag_ref,
        tag_object,
        tag="corpus-data-2026-08-30-r1",
        target="a" * 40,
        object_id="c" * 64,
    )

    lightweight = {"object": {"type": "commit", "sha": "a" * 40}}
    with pytest.raises(ReleaseTransactionError, match="annotated"):
        verify_annotated_tag(
            lightweight,
            tag_object,
            tag="corpus-data-2026-08-30-r1",
            target="a" * 40,
            object_id="c" * 64,
        )
