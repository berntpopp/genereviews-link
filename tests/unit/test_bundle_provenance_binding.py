"""The bundle producer revision is distinct from immutable compute-run history."""

from __future__ import annotations

import pytest

from genereview_link.corpus.bundle_integrity import BundleIntegrityError
from genereview_link.corpus.bundle_provenance import computation_source_revision


def test_v3_keeps_the_legacy_single_revision_binding() -> None:
    revision = "a" * 40
    assert (
        computation_source_revision(
            manifest_version="3",
            provenance_model=None,
            bundle_revision=revision,
            computation_revision=revision,
            provenance_revision=revision,
        )
        == revision
    )
    with pytest.raises(BundleIntegrityError, match="legacy revision binding"):
        computation_source_revision(
            manifest_version="3",
            provenance_model=None,
            bundle_revision="b" * 40,
            computation_revision=revision,
            provenance_revision=revision,
        )


def test_v4_explicitly_binds_bundle_and_historical_computation_revisions() -> None:
    compute_revision = "a" * 40
    bundle_revision = "b" * 40
    assert (
        computation_source_revision(
            manifest_version="4",
            provenance_model="bundle-producer-and-historical-computation-v1",
            bundle_revision=bundle_revision,
            computation_revision=compute_revision,
            provenance_revision=compute_revision,
        )
        == compute_revision
    )

    with pytest.raises(BundleIntegrityError, match="computation revision"):
        computation_source_revision(
            manifest_version="4",
            provenance_model="bundle-producer-and-historical-computation-v1",
            bundle_revision=bundle_revision,
            computation_revision=compute_revision,
            provenance_revision="c" * 40,
        )
    with pytest.raises(BundleIntegrityError, match="provenance model"):
        computation_source_revision(
            manifest_version="4",
            provenance_model="unrecognized",
            bundle_revision=bundle_revision,
            computation_revision=compute_revision,
            provenance_revision=compute_revision,
        )
