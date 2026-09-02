"""Where one sealed corpus bundle sits in the release chain.

The seal is what the *next* build points at, so it states its own position
explicitly rather than leaving it to be inferred: ``genesis: true`` with
``prior: null`` for the first build of a chain, otherwise the exact prior
object/manifest/release identity the build was proven against. Rights are
unaffected -- they stay bound to the object ID by ``rights.py``.
"""

from __future__ import annotations

from genereview_link.corpus.handoff import HandoffError

PRIOR_IDENTITY_FIELDS = ("object_id", "manifest_sha256", "corpus_release_id")


def chain_position(source_manifest: dict[str, object]) -> tuple[bool, dict[str, object] | None]:
    """Read the sealed bundle's own position in the corpus chain from its manifest."""
    capture = source_manifest.get("source_capture")
    if not isinstance(capture, dict):
        raise HandoffError("manifest.json lacks the retained source capture")
    genesis = capture.get("genesis", False)
    prior = capture.get("prior_artifact")
    if genesis is not True and genesis is not False:
        raise HandoffError("manifest.json genesis flag must be a literal boolean")
    if genesis:
        if prior is not None:
            raise HandoffError("a genesis bundle must not name a prior artifact")
        return True, None
    if not isinstance(prior, dict):
        raise HandoffError("a chained bundle must name its prior artifact")
    if any(not isinstance(prior.get(key), str) or not prior[key] for key in PRIOR_IDENTITY_FIELDS):
        raise HandoffError("manifest.json prior artifact identity is incomplete")
    return False, {key: prior[key] for key in PRIOR_IDENTITY_FIELDS}


__all__ = ["PRIOR_IDENTITY_FIELDS", "chain_position"]
