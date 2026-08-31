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
    assert build["timeout-minutes"] == 90
    assert build["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
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
    assert "release_assets" not in scripts
    assert "gh release download" not in scripts
    assert "genereview-link ingest" in scripts
    assert "--index-only" in scripts
    assert "evaluation" in scripts
    assert "actions/attest-build-provenance@" in "\n".join(
        str(step.get("uses", "")) for step in steps
    )


def test_data_release_publisher_accepts_only_sealed_rights_bound_handoff() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    assert publish["timeout-minutes"] == 90
    assert publish["permissions"] == {
        "actions": "read",
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert "github.ref == 'refs/heads/main'" in str(publish["if"])
    steps = publish["steps"]
    assert any(str(step.get("uses", "")).startswith("actions/checkout@") for step in steps)
    scripts = "\n".join(str(step.get("run", "")) for step in steps)
    assert "GENEREVIEWS_RIGHTS_RECORD_JSON" in scripts
    assert "publish-handoff" in scripts
    assert "verify_handoff" in scripts
    assert "gh attestation verify" in scripts
    assert "attest=" not in scripts
    assert "semantic" in scripts.lower()
    assert "corpus_restore" in scripts
    assert "HTTP 404" in scripts
    assert "repos/$GH_REPO/immutable-releases" in scripts
    assert "jq -e '.enabled == true'" in scripts
    assert "gh release delete" not in scripts
    assert "published_noop" in scripts
    assert 'test "$match_count" -le 1' in scripts
    assert 'find "$RUNNER_TEMP/sealed/publisher-tool"' not in scripts
    assert "uvx --from" not in scripts
    assert "--no-index" in scripts
    assert "--no-deps" in scripts
    assert "publisher-dependencies" not in scripts
    assert "pip download" not in scripts
    assert 'chmod 0700 "$handoff_root"' in scripts
    assert 'chmod 0500 "$handoff_object"' in scripts
    assert 'chmod 0400 "$handoff_object/$name"' in scripts
    build = workflow["jobs"]["build"]
    assert isinstance(build, dict)
    build_scripts = "\n".join(str(step.get("run", "")) for step in build["steps"])
    assert "pip download" not in build_scripts
    assert "PUBLISHER_ENV" in scripts


def test_publisher_uses_protected_secret_not_repository_variable() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    text = str(publish.get("env", {}).get("GENEREVIEWS_RIGHTS_RECORD_JSON", ""))
    assert text == "${{ secrets.GENEREVIEWS_RIGHTS_RECORD_JSON }}"
    assert "vars.GENEREVIEWS_RIGHTS_RECORD_JSON" not in str(publish)


def test_each_promotion_path_uses_exact_release_and_tag_identities() -> None:
    steps = _workflow()["jobs"]["publish"]["steps"]
    gate = next(
        step for step in steps if step.get("name") == "Four-state immutable publication gate"
    )
    script = gate["run"]
    assert "releases/tags/" not in script
    assert "gh release create" not in script
    assert "gh release edit" not in script
    assert "releases?per_page=100&page=$page" in script
    assert "repos/$GH_REPO/releases/$release_id" in script
    assert '--method PATCH "repos/$GH_REPO/releases/$release_id"' in script
    assert "uploads.github.com/repos/$GH_REPO/releases/$release_id/assets" in script
    assert "repos/$GH_REPO/git/ref/tags/$tag" in script
    assert "refs/tags/$tag" in script
    assert ".draft == true and .immutable == false and .published_at == null" in script
    assert '.draft == false and .immutable == true and (.published_at | type == "string")' in script
    assert script.count("verify_remote") >= 3
    assert "require_exact_tag" in script
    assert "published_noop: exact immutable release verified" in script


def test_dispatch_validation_cannot_skip_malformed_input_combinations() -> None:
    workflow = _workflow()
    validate = workflow["jobs"]["validate"]
    assert validate["if"] == "${{ always() }}"
    assert validate["permissions"] == {"contents": "read"}
    assert "object_id" in str(validate["steps"])
    assert "handoff_run_id" in str(validate["steps"])
    assert workflow["jobs"]["build"]["needs"] == "validate"
    assert workflow["jobs"]["publish"]["needs"] == "validate"
