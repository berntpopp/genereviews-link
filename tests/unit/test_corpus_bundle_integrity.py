"""Closed-world integrity checks for a published three-file corpus bundle.

Publication is now an ordinary ``gh release create`` of ``corpus.dump``,
``manifest.json`` and ``SHA256SUMS``: no sealed handoff object, no locator, no second
signature. That makes this module the whole integrity story for a downloaded release,
so its refusals are exercised here directly rather than through the deleted
sealed-handoff machinery that used to wrap them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from genereview_link.corpus.bundle import BundleManifest, write_data_only_bundle
from genereview_link.corpus.bundle_integrity import (
    BUNDLE_FILES,
    BundleIntegrityError,
    verify_data_only_bundle,
)


def _bundle(tmp_path: Path, **manifest_fields: object) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP data-only")
    return write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "release",
        manifest=BundleManifest(corpus_release_id="2026-08-30-r1", **manifest_fields),  # type: ignore[arg-type]
    )


def _reseal(bundle: Path, payload: dict[str, object]) -> None:
    """Rewrite manifest.json and its SHA256SUMS line so only the semantics are wrong."""
    metadata = bundle / "manifest.json"
    metadata.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    dump_digest = hashlib.sha256((bundle / "corpus.dump").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(
        f"{dump_digest}  corpus.dump\n"
        f"{hashlib.sha256(metadata.read_bytes()).hexdigest()}  manifest.json\n"
    )


def test_bundle_file_set_is_exactly_three_names() -> None:
    assert set(BUNDLE_FILES) == {"corpus.dump", "manifest.json", "SHA256SUMS"}


def test_an_extra_or_missing_file_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "publisher-tool.whl").write_bytes(b"unreviewed")

    with pytest.raises(BundleIntegrityError, match="exactly the required regular files"):
        verify_data_only_bundle(bundle)

    (bundle / "publisher-tool.whl").unlink()
    (bundle / "SHA256SUMS").unlink()
    with pytest.raises(BundleIntegrityError, match="exactly the required regular files"):
        verify_data_only_bundle(bundle)


def test_a_symlinked_bundle_member_is_never_followed(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    elsewhere = tmp_path / "elsewhere.dump"
    elsewhere.write_bytes(b"PGDMP data-only")
    (bundle / "corpus.dump").unlink()
    (bundle / "corpus.dump").symlink_to(elsewhere)

    with pytest.raises(BundleIntegrityError, match="unsafe or missing"):
        verify_data_only_bundle(bundle)


def test_a_substituted_dump_is_caught_by_the_published_checksums(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "corpus.dump").write_bytes(b"PGDMP substituted")

    with pytest.raises(BundleIntegrityError, match=r"checksum mismatch for corpus\.dump"):
        verify_data_only_bundle(bundle)


@pytest.mark.parametrize(
    ("sums", "message"),
    [
        pytest.param("", "SHA256SUMS is incomplete", id="empty"),
        pytest.param("not a checksum line\n", "invalid checksum line", id="unparsable"),
        pytest.param("{}  corpus.dump\n".format("z" * 64), "invalid checksum line", id="non-hex"),
        pytest.param(
            "{}  corpus.dump\n".format("a" * 64), "SHA256SUMS is incomplete", id="incomplete"
        ),
        pytest.param(
            "{0}  corpus.dump\n{0}  corpus.dump\n".format("a" * 64),
            "exactly once",
            id="duplicate",
        ),
        pytest.param(
            "{0}  corpus.dump\n{0}  manifest.json\n{0}  publisher-tool.whl\n".format("a" * 64),
            "exactly once",
            id="unexpected-name",
        ),
    ],
)
def test_a_malformed_checksum_file_fails_closed(tmp_path: Path, sums: str, message: str) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "SHA256SUMS").write_text(sums)

    with pytest.raises(BundleIntegrityError, match=message):
        verify_data_only_bundle(bundle)


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(lambda payload: payload.update(attacker_field=True), id="extra-field"),
        pytest.param(lambda payload: payload.pop("app_git_sha"), id="missing-field"),
        pytest.param(
            lambda payload: payload.update(created_at="2026-08-30T00:00:00Z"), id="volatile-field"
        ),
    ],
)
def test_the_published_manifest_is_closed_world_validated(
    tmp_path: Path, mutator: Callable[[dict[str, object]], object]
) -> None:
    """No field may be added, dropped, or smuggled back in -- including ``created_at``.

    A build timestamp is not artifact identity, and admitting one would make two
    otherwise identical bundles disagree, so the field set is compared exactly against
    the reviewed dataclass rather than merely searched for known keys.
    """
    bundle = _bundle(tmp_path)
    payload = json.loads((bundle / "manifest.json").read_text())
    mutator(payload)
    _reseal(bundle, payload)

    with pytest.raises(BundleIntegrityError, match="missing, extra, or volatile fields"):
        verify_data_only_bundle(bundle)


def test_a_manifest_field_of_the_wrong_type_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    payload = json.loads((bundle / "manifest.json").read_text())
    payload["passage_count"] = "41414"
    _reseal(bundle, payload)

    with pytest.raises(BundleIntegrityError, match="invalid type: passage_count"):
        verify_data_only_bundle(bundle)


def test_only_a_v3_data_only_manifest_is_accepted(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    payload = json.loads((bundle / "manifest.json").read_text())
    payload["bundle_format"] = "tar.gz"
    _reseal(bundle, payload)

    with pytest.raises(BundleIntegrityError, match="not a v3 data-only bundle"):
        verify_data_only_bundle(bundle)


def test_build_provenance_and_the_rights_notice_are_checked_before_deeper_identity(
    tmp_path: Path,
) -> None:
    """A bundle that clears the structural checks still has to be honest about itself.

    ``build_provenance`` and ``rights_notice`` are checked immediately after the shape
    checks, so a bundle that lies about how it was built is refused long before the
    expensive identity re-derivation would have rejected it for some other reason.
    """
    bundle = _bundle(tmp_path, build_provenance="ci-attested")
    payload = json.loads((bundle / "manifest.json").read_text())
    _reseal(bundle, payload)

    with pytest.raises(BundleIntegrityError, match="maintainer-prebuilt"):
        verify_data_only_bundle(bundle)

    payload["build_provenance"] = "maintainer-prebuilt"
    _reseal(bundle, payload)
    # The default manifest carries an empty rights notice, which is not the committed
    # one and must be refused rather than treated as "no claim made".
    with pytest.raises(BundleIntegrityError, match="rights notice is invalid"):
        verify_data_only_bundle(bundle)
