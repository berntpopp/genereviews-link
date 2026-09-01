"""Security contract for the corpus data-release transformation workflow."""

from __future__ import annotations

import os
import subprocess
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
    assert "uv sync --group dev --extra cpu --frozen" in scripts
    assert "BGE_MODEL_REVISION" in scripts
    assert "BGE_MODEL_FILES" in scripts
    assert "HF_HUB_OFFLINE=1" in scripts
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
    assert "GENEREVIEWS_SOURCE_LOCATOR" in scripts
    assert "--archive" in scripts and "--source-metadata" in scripts
    assert "pg18-client" in scripts
    assert "--index-only" in scripts
    assert "evaluation" in scripts
    assert "actions/attest-build-provenance@" in "\n".join(
        str(step.get("uses", "")) for step in steps
    )


def test_data_release_publisher_accepts_only_sealed_rights_bound_handoff() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    assert publish["timeout-minutes"] == 240
    assert publish["permissions"] == {
        "actions": "write",
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert "github.ref == 'refs/heads/main'" in str(publish["if"])
    steps = publish["steps"]
    assert not any(str(step.get("uses", "")).startswith("actions/checkout@") for step in steps)
    scripts = "\n".join(str(step.get("run", "")) for step in steps)
    assert "GENEREVIEWS_RIGHTS_LOCATOR" in scripts
    assert "rights-evidence" in scripts
    assert "terms-snapshot" in scripts
    assert "publisher-tool.whl" in scripts
    assert "publish-handoff" in scripts
    assert "verify_handoff" in scripts
    assert "gh attestation verify" in scripts
    assert "seal-retention-receipt.json" in scripts
    assert (
        'for subject in "$handoff_object/corpus.dump" "$seal_manifest" "$tool" "$receipt"'
        in scripts
    )
    assert "attest=" not in scripts
    assert "dispatch_verifier prepublication" in scripts
    assert "genereview_restore" not in scripts
    assert "tag-status" in scripts
    assert "repos/$GH_REPO/immutable-releases" in scripts
    assert "jq -e '.enabled == true'" in scripts
    assert "gh release delete" not in scripts
    assert "published_noop" in scripts
    assert 'test "$match_count" -le 1' in scripts
    assert 'find "$RUNNER_TEMP/sealed/publisher-tool"' not in scripts
    assert "uvx --from" not in scripts
    assert "sealed wheel exceeds extraction bounds" in scripts
    assert "uv sync" not in scripts
    assert "uv run" not in scripts
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
    assert "python3 -I" in scripts
    assert "sys.path.insert(0" in scripts
    assert "module_file" in scripts
    assert "PYTHONPATH=" not in scripts
    assert (
        "retention-days: 90" not in (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    )


def test_publisher_uses_protected_secret_not_repository_variable() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    text = str(publish.get("env", {}).get("GENEREVIEWS_RIGHTS_LOCATOR", ""))
    assert text == "${{ secrets.GENEREVIEWS_RIGHTS_LOCATOR }}"
    assert "vars.GENEREVIEWS_RIGHTS_LOCATOR" not in str(publish)


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
    assert '-f ref="refs/tags/$tag"' not in script
    assert 'test "$(cat "$tag_precondition")" = 404' in script
    assert ".draft == true and .immutable == false and .published_at == null" in script
    assert '.draft == false and .immutable == true and (.published_at | type == "string")' in script
    assert script.count("verify_remote") >= 3
    assert "require_exact_tag" in script
    assert "published_noop: exact immutable release verified" in script
    assert "If-Match: $verified_etag" in script
    assert "If-None-Match: $verified_etag" in script
    assert "precondition" in script.lower()
    assert "post-publication" in script.lower()
    assert "openssl rand -hex 32" in script
    assert ".createdAt >=" in script
    assert ".headSha ==" in script
    assert "verify-corpus-bundle.yml" in script
    assert "corpus-publication-verification" in script


def test_external_verifier_uses_frozen_cpu_runtime_and_exact_retained_sources() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    verify = workflow["jobs"]["verify"]
    scripts = "\n".join(str(step.get("run", "")) for step in verify["steps"])

    assert "uv sync --group dev --extra cpu --frozen" in scripts
    assert "BGE_MODEL_FILES" in scripts and "BGE_MODEL_REVISION" in scripts
    assert "HF_HUB_OFFLINE=1" in scripts and "TRANSFORMERS_OFFLINE=1" in scripts
    assert "GENEREVIEWS_SOURCE_LOCATOR" in scripts
    assert "fetch_source_assets" in scripts
    assert "load_offline_capture" in scripts
    assert "evaluate_connection" in scripts
    assert "tests/eval/run_eval.py" not in scripts


def test_release_suffix_selection_is_identity_aware_and_exhaustive() -> None:
    workflow = _workflow()
    scripts = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["build"]["steps"])
    assert "git/matching-refs/tags/corpus-data-" in scripts
    assert "source identity" in scripts.lower()
    assert "published_noop" in scripts
    assert "first_free_release_id" in scripts
    assert "max_suffix" in scripts and "reviewed exhaustive bound" in scripts
    assert "rights-record.json" in scripts
    assert "existing rights record is not the exact candidate identity" in scripts


def test_dispatch_validation_cannot_skip_malformed_input_combinations() -> None:
    workflow = _workflow()
    validate = workflow["jobs"]["validate"]
    assert validate["if"] == "${{ always() }}"
    assert validate["permissions"] == {"contents": "read"}
    assert "object_id" in str(validate["steps"])
    assert "handoff_run_id" in str(validate["steps"])
    assert workflow["jobs"]["build"]["needs"] == "validate"
    assert workflow["jobs"]["publish"]["needs"] == "validate"


def test_dispatch_shell_state_machine_executes_all_four_input_states(tmp_path: Path) -> None:
    workflow = _workflow()
    step = workflow["jobs"]["validate"]["steps"][0]
    script = step["run"]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "external-calls"
    for command in ("gh", "curl", "docker", "psql"):
        shim = fake_bin / command
        shim.write_text(f"#!/bin/sh\nprintf '%s\\n' {command} >>'{calls}'\nexit 97\n")
        shim.chmod(0o755)

    states = (
        ("", "", "refs/heads/topic", 0),
        ("a" * 64, "42", "refs/heads/main", 0),
        ("a" * 64, "", "refs/heads/main", 1),
        ("not-a-digest", "42", "refs/heads/topic", 1),
    )
    for object_id, run_id, ref_name, expected in states:
        result = subprocess.run(  # noqa: S603 - repository workflow shell is the test subject
            ["/bin/bash", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "OBJECT_ID": object_id,
                "HANDOFF_RUN_ID": run_id,
                "REF_NAME": ref_name,
            },
        )
        assert (result.returncode == 0) is (expected == 0), result.stderr

    assert not calls.exists(), "input validation unexpectedly invoked external tooling"
