"""Security contract for the corpus data-release transformation workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict[str, object]:
    parsed = yaml.safe_load((ROOT / ".github/workflows/corpus-data-release.yml").read_text())
    assert isinstance(parsed, dict)
    return parsed


def test_data_release_build_is_data_only_and_unprivileged() -> None:
    workflow = _workflow()
    build = workflow["jobs"]["build"]
    assert isinstance(build, dict)
    assert build["permissions"] == {"contents": "read"}
    service = build["services"]["postgres"]
    assert "@sha256:" in service["image"]
    steps = build["steps"]
    scripts = "\n".join(str(step.get("run", "")) for step in steps)
    assert "--data-only" in scripts
    assert "--no-owner" in scripts
    assert "--no-privileges" in scripts
    assert "--single-transaction" in scripts
    assert "--exit-on-error" in scripts
    assert "read_archive_entries" in scripts
    assert "assert_data_only_archive" in scripts
    assert "pg_restore" in scripts and "|| true" not in scripts
    assert "release_assets" in scripts
    assert "gh release download" not in scripts


def test_data_release_publisher_accepts_only_sealed_rights_bound_handoff() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    assert publish["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    steps = publish["steps"]
    assert not any(str(step.get("uses", "")).startswith("actions/checkout@") for step in steps)
    scripts = "\n".join(str(step.get("run", "")) for step in steps)
    assert "GENEREVIEWS_RIGHTS_RECORD_JSON" in scripts
    assert "publish-handoff" in scripts
    assert "verify_handoff" in scripts
    assert "gh attestation verify" in scripts
    assert "HTTP 404" in scripts
    assert "gh release delete" not in scripts
    assert "draft_publish_existing" in scripts
    assert "published_noop" in scripts
    assert "collision" in scripts
