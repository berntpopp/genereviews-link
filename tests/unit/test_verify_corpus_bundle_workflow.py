"""Security contract for the data-only corpus verification workflow.

The workflow's job is to take nothing but a release tag and re-derive everything: it
downloads the three published assets, proves them against ``SHA256SUMS`` and against
the manifest, checks the honest ``build_provenance`` and the committed rights notice,
then restores the dump into a fresh PostgreSQL 18 and re-computes the counts, the
content identity, the computation chain and the evaluation results from reviewed code.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/verify-corpus-bundle.yml"


def _workflow() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(workflow, dict)
    return workflow


def test_verifier_takes_only_a_release_tag_and_never_writes() -> None:
    workflow = _workflow()
    # PyYAML parses the bare `on:` key as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)

    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"release_tag"}
    assert inputs["release_tag"]["required"] is True
    assert workflow["permissions"] == {"contents": "read"}


def test_verifier_downloads_exactly_the_three_published_assets_and_proves_them() -> None:
    verify = _workflow()["jobs"]["verify"]
    assert isinstance(verify, dict)
    scripts = "\n".join(str(step.get("run", "")) for step in verify["steps"])

    assert verify["timeout-minutes"] == 90
    assert "@sha256:" in verify["services"]["postgres"]["image"]
    assert verify["env"]["EXPECTED_RELEASE_TAG"] == "${{ inputs.release_tag }}"
    assert "gh release download" in scripts
    for name in ("corpus.dump", "manifest.json", "SHA256SUMS"):
        assert f"--pattern {name}" in scripts
    assert "sha256sum -c SHA256SUMS" in scripts
    # The release itself must be immutable-shaped: published, not a prerelease, and
    # carrying exactly those three assets and nothing else.
    assert ".isDraft == false and .isPrerelease == false" in scripts
    assert '["SHA256SUMS","corpus.dump","manifest.json"]' in scripts
    # The release must point at the revision the manifest names as its builder.
    assert ".targetCommitish" in scripts and ".app_git_sha" in scripts


def test_verifier_checks_provenance_and_the_committed_rights_notice() -> None:
    scripts = "\n".join(str(step.get("run", "")) for step in _workflow()["jobs"]["verify"]["steps"])

    assert "from genereview_link.corpus.bundle_integrity import verify_data_only_bundle" in scripts
    assert "verify_data_only_bundle(Path(sys.argv[1]))" in scripts
    assert "build_provenance" in scripts
    assert "rights_notice" in scripts
    # There is no CI build behind these bytes, so no attestation may be claimed.
    assert "gh attestation verify" not in scripts
    assert "attest-build-provenance" not in WORKFLOW.read_text()


def test_verifier_rebuilds_schema_and_restores_data_only_under_a_restricted_role() -> None:
    verify = _workflow()["jobs"]["verify"]
    scripts = "\n".join(str(step.get("run", "")) for step in verify["steps"])

    assert "genereview-link db migrate" in scripts
    assert "ensure_restore_role" in scripts
    assert "pg_restore" in scripts and "|| true" not in scripts
    assert '"$RESTORE_DATABASE_URL"' in scripts
    for flag in ("--data-only", "--no-owner", "--no-privileges", "--single-transaction"):
        assert flag in scripts
    assert "--exit-on-error" in scripts


def test_verifier_rederives_every_manifest_claim_from_the_restored_database() -> None:
    scripts = "\n".join(str(step.get("run", "")) for step in _workflow()["jobs"]["verify"]["steps"])

    assert "genereview_embeddings_bge384_hnsw_cosine" in scripts
    assert "chapter_count" in scripts
    assert "passage_count" in scripts
    assert "representative" in scripts
    assert "\\quit 1" in scripts
    assert "hnsw_present" in scripts
    assert "\\gset" in scripts
    assert '-v EXPECTED_CHAPTER_COUNT="$EXPECTED_CHAPTER_COUNT"' in scripts
    assert '-v EXPECTED_PASSAGE_COUNT="$EXPECTED_PASSAGE_COUNT"' in scripts
    assert '-v EXPECTED_EMBEDDING_COUNT="$EXPECTED_EMBEDDING_COUNT"' in scripts
    assert "active_source_identity" in scripts
    assert "model_revision" in scripts
    assert "side_data" in scripts
    assert "collect_content_identity" in scripts
    assert "load_active_computation" in scripts
    assert "evaluate_connection" in scripts
    assert "write_release_readiness" in scripts
