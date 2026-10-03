"""Versioned provenance bindings for immutable corpus manifests."""

from __future__ import annotations

import re

from genereview_link.corpus.bundle_integrity import BundleIntegrityError

PROVENANCE_MODEL_V4 = "bundle-producer-and-historical-computation-v1"


def computation_source_revision(
    *,
    manifest_version: str,
    provenance_model: object,
    bundle_revision: str,
    computation_revision: str,
    provenance_revision: str,
) -> str:
    """Validate whether this manifest version keeps or separates source identities."""
    if manifest_version == "3":
        if (
            provenance_model is not None
            or bundle_revision != computation_revision
            or computation_revision != provenance_revision
        ):
            raise BundleIntegrityError("manifest-v3 legacy revision binding is invalid")
    elif manifest_version == "4":
        if provenance_model != PROVENANCE_MODEL_V4:
            raise BundleIntegrityError("manifest-v4 provenance model is unsupported")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", computation_revision)
            or computation_revision != provenance_revision
        ):
            raise BundleIntegrityError("manifest-v4 computation revision is invalid")
    else:
        raise BundleIntegrityError("manifest version is unsupported")
    return computation_revision
