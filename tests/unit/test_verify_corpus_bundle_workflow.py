"""Security contract for the data-only corpus verification workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_verifier_uses_exact_assets_and_rebuilds_schema_before_restore() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    assert isinstance(workflow, dict)
    verify = workflow["jobs"]["verify"]
    assert isinstance(verify, dict)
    assert verify["timeout-minutes"] == 90
    service = verify["services"]["postgres"]
    assert "@sha256:" in service["image"]
    scripts = "\n".join(str(step.get("run", "")) for step in verify["steps"])
    assert "release_assets" in scripts
    assert "gh release download" not in scripts
    assert "${{ inputs.release_tag }}" not in scripts
    download = next(
        step for step in verify["steps"] if "Bounded fresh-directory" in step.get("name", "")
    )
    assert download["env"]["RELEASE_TAG"] == "${{ inputs.release_tag }}"
    assert "genereview-link db migrate" in scripts
    assert "--data-only" in scripts
    assert "--no-owner" in scripts
    assert "--no-privileges" in scripts
    assert "--single-transaction" in scripts
    assert "--exit-on-error" in scripts
    assert "read_archive_entries" in scripts
    assert "assert_data_only_archive" in scripts
    assert "gh release download" not in scripts
    assert "pg_restore" in scripts and "|| true" not in scripts
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
