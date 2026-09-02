"""The committed redistribution rights notice and its binding into the manifest.

``data/RIGHTS.json`` replaced the two-person, per-release rights record: the maintainer
reviews the upstream GeneReviews terms once, commits the determination, and every
published bundle carries it verbatim. These tests hold that notice to the same
fail-closed standard the old ceremony was held to -- exact field set, HTTPS-only
references, an explicit research-use-only restriction, and a canonical digest that a
verifier can compare byte for byte.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from genereview_link.corpus.bundle_integrity import BundleIntegrityError
from genereview_link.corpus.bundle_verifier import (
    MAINTAINER_PREBUILT,
    _verify_build_provenance,
    _verify_rights_notice,
)
from genereview_link.corpus.rights_notice import (
    DEFAULT_RIGHTS_PATH,
    MAX_RIGHTS_BYTES,
    RightsNoticeError,
    load_rights_notice,
    validate_rights_notice,
)

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "data" / "RIGHTS.json"


def _notice() -> dict[str, object]:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_notice_is_present_and_well_shaped() -> None:
    notice = load_rights_notice(COMMITTED)

    assert notice.digest.startswith("sha256:")
    assert notice.license_url.startswith("https://")
    assert notice.terms_url == "https://www.ncbi.nlm.nih.gov/books/NBK138602/"
    assert date.fromisoformat(notice.terms_reviewed_at) <= date.today()
    assert "GeneReviews" in notice.attribution
    # The determination is an explicit, named repository-owner review -- not an upstream
    # approval and not an anonymous default.
    assert notice.reviewer.strip()
    assert "research use only" in notice.use_restriction.casefold()


def test_default_path_resolves_to_the_committed_notice() -> None:
    assert DEFAULT_RIGHTS_PATH == COMMITTED
    assert load_rights_notice().digest == load_rights_notice(DEFAULT_RIGHTS_PATH).digest


def test_notice_digest_is_canonical_and_order_independent() -> None:
    shuffled = dict(reversed(list(_notice().items())))

    assert validate_rights_notice(shuffled).digest == load_rights_notice().digest


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(lambda notice: notice.pop("attribution"), id="missing-field"),
        pytest.param(lambda notice: notice.pop("reviewer"), id="missing-reviewer"),
        pytest.param(lambda notice: notice.pop("use_restriction"), id="missing-restriction"),
        pytest.param(lambda notice: notice.update(extra=True), id="extra-field"),
        pytest.param(lambda notice: notice.update(schema_version=2), id="schema-version"),
        pytest.param(lambda notice: notice.update(schema_version="1"), id="schema-version-string"),
        pytest.param(lambda notice: notice.update(dataset=""), id="empty-dataset"),
        pytest.param(lambda notice: notice.update(citation=""), id="empty-citation"),
        pytest.param(lambda notice: notice.update(reviewer="  "), id="blank-reviewer"),
        pytest.param(
            lambda notice: notice.update(
                use_restriction="redistribute freely for any commercial purpose"
            ),
            id="permissive-restriction",
        ),
        pytest.param(
            lambda notice: notice.update(terms_url="http://www.ncbi.nlm.nih.gov/books/NBK138602/"),
            id="plaintext-terms-url",
        ),
        pytest.param(
            lambda notice: notice.update(source_url="ftp://ftp.ncbi.nlm.nih.gov/"),
            id="non-https-source-url",
        ),
        pytest.param(
            # A filesystem reference is not transferable to anyone who downloads the
            # release, so it can never stand in for the published terms.
            lambda notice: notice.update(terms_url="file:///tmp/terms-snapshot.html"),
            id="filesystem-terms-url",
        ),
        pytest.param(
            lambda notice: notice.update(license={"name": "GeneReviews"}), id="partial-license"
        ),
        pytest.param(
            lambda notice: notice.update(
                license={**notice["license"], "url": "http://example.invalid/"}  # type: ignore[dict-item]
            ),
            id="plaintext-license-url",
        ),
        pytest.param(
            lambda notice: notice.update(terms_reviewed_at="not-a-date"), id="unparsable-date"
        ),
        pytest.param(
            lambda notice: notice.update(terms_reviewed_at="2999-01-01"), id="future-date"
        ),
    ],
)
def test_every_single_field_mutation_fails_closed(
    mutator: Callable[[dict[str, object]], object],
) -> None:
    notice = _notice()
    mutator(notice)

    with pytest.raises(RightsNoticeError):
        validate_rights_notice(notice)


def test_missing_or_unparsable_notice_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RightsNoticeError, match="missing"):
        load_rights_notice(tmp_path / "absent.json")

    unparsable = tmp_path / "RIGHTS.json"
    unparsable.write_text("{", encoding="utf-8")
    with pytest.raises(RightsNoticeError, match="valid JSON"):
        load_rights_notice(unparsable)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schema_version":1,"schema_version":2}')
    with pytest.raises(RightsNoticeError, match="valid JSON"):
        load_rights_notice(duplicate)

    deep = tmp_path / "deep.json"
    deep.write_bytes(b"[" * 10_000 + b"]" * 10_000)
    with pytest.raises(RightsNoticeError, match="valid JSON"):
        load_rights_notice(deep)


def test_oversized_or_symlinked_notice_fails_closed(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_RIGHTS_BYTES + 1))
    with pytest.raises(RightsNoticeError, match="size limit"):
        load_rights_notice(oversized)

    link = tmp_path / "link.json"
    link.symlink_to(COMMITTED)
    with pytest.raises(RightsNoticeError):
        load_rights_notice(link)


def _manifest() -> dict[str, object]:
    return {"build_provenance": MAINTAINER_PREBUILT, "rights_notice": _notice()}


def test_verifier_accepts_the_committed_notice_and_honest_provenance() -> None:
    manifest = _manifest()
    _verify_build_provenance(manifest)
    _verify_rights_notice(manifest)


@pytest.mark.parametrize(
    "claim",
    ["", "ci-attested", "github-actions", "MAINTAINER-PREBUILT", None],
    ids=("empty", "attested", "actions", "wrong-case", "absent"),
)
def test_verifier_rejects_any_other_build_provenance_claim(claim: object) -> None:
    manifest = _manifest()
    if claim is None:
        del manifest["build_provenance"]
    else:
        manifest["build_provenance"] = claim

    with pytest.raises(BundleIntegrityError, match=MAINTAINER_PREBUILT):
        _verify_build_provenance(manifest)


def test_verifier_rejects_a_published_notice_that_is_not_the_committed_one() -> None:
    manifest = _manifest()
    published = manifest["rights_notice"]
    assert isinstance(published, dict)
    published["reviewer"] = "somebody else"

    with pytest.raises(BundleIntegrityError, match="does not match the committed"):
        _verify_rights_notice(manifest)


def test_verifier_rejects_a_published_notice_that_is_not_a_valid_notice() -> None:
    manifest = _manifest()
    published = manifest["rights_notice"]
    assert isinstance(published, dict)
    del published["use_restriction"]

    with pytest.raises(BundleIntegrityError, match="rights notice is invalid"):
        _verify_rights_notice(manifest)

    with pytest.raises(BundleIntegrityError, match="rights notice is invalid"):
        _verify_rights_notice({"rights_notice": None})
