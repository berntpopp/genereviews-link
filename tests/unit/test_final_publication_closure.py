"""Regressions for the final immutable GeneReviews publication closure."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest

import genereview_link.corpus.pipeline as pipeline
from genereview_link.corpus.release_transaction import (
    ReleaseTransactionError,
    _expected_assets,
)
from genereview_link.corpus.rights import RightsError, verify_rights_record
from genereview_link.db.restore import ArchivePolicyError, extract_bundle

ROOT = Path(__file__).resolve().parents[2]
RIGHTS_ATTRIBUTION = (
    "GeneReviews® content ©1993-2026 University of Washington, Seattle; "
    "source https://www.genereviews.org; noncommercial research purposes only; "
    "comply with the copyright notice and Usage Disclaimer; no further modifications."
)


def test_release_transaction_accepts_only_the_literal_ordered_asset_set() -> None:
    digest = "sha256:" + "1" * 64
    attacker = {f"attacker-{index}": {"size": 1, "digest": digest} for index in range(1, 9)}

    with pytest.raises(ReleaseTransactionError, match="exact"):
        _expected_assets(attacker)

    expected = {
        name: {"size": index + 1, "digest": digest}
        for index, name in enumerate(
            (
                "corpus.dump",
                "manifest.json",
                "SHA256SUMS",
                "rights-record.json",
                "rights-evidence.json",
                "terms-snapshot.html",
                "seal-manifest.json",
                "publisher-tool.whl",
            )
        )
    }
    assert tuple(_expected_assets(expected)) == tuple(expected)


def test_production_image_copies_pg18_clients_without_mutable_pgdg_repository() -> None:
    dockerfile = (ROOT / "docker/Dockerfile").read_text()

    assert "apt.postgresql.org" not in dockerfile
    assert "COPY --from=pg18-client /usr/lib/postgresql/18/bin" in dockerfile
    assert "COPY --from=pg18-client /usr/share/postgresql/18" in dockerfile
    assert "COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libpq.so.5" in dockerfile


def test_workflows_produce_and_consume_bound_attestations_for_all_restore_inputs() -> None:
    producer = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    verifier = (ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text()

    assert "name: Attest the exact evaluated build output" in producer
    producer_subjects = producer.split("name: Attest the exact evaluated build output", 1)[1]
    producer_subjects = producer_subjects.split("- name:", 1)[0]
    for name in (
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "seal-manifest.json",
        "publisher-tool.whl",
    ):
        assert name in producer_subjects
    assert "--source-ref refs/heads/main" in verifier
    assert 'for subject in "$verify_dir/corpus.dump" "$verify_dir/manifest.json"' in verifier


def test_production_seed_contract_preserves_current_pin_and_supports_direct_assets() -> None:
    compose = (ROOT / "docker/docker-compose.yml").read_text()
    smoke = (ROOT / "docker/ci-prepare-smoke.sh").read_text()
    config = json.loads((ROOT / "container-release.json").read_bytes())
    docs = (ROOT / "docs/deployment.md").read_text() + (ROOT / "docs/configuration.md").read_text()

    assert "CORPUS_SEED_PATH" in compose and "CORPUS_DUMP_SHA256" in compose
    assert "corpus-bundle.tar.gz)" in smoke and "corpus.dump)" in smoke
    assert config["data"]["release_tag"] == "corpus-data-2026-07-13-r1"
    assert config["data"]["digest"] == (
        "sha256:4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc"
    )
    assert config["data_identity_contract"] == "unadopted"
    assert "SHA256SUMS" in docs and "legacy" in docs


@pytest.mark.parametrize("mode", ["legacy", "direct"])
def test_smoke_seed_preparation_stages_the_configured_release_assets(
    tmp_path: Path, mode: str
) -> None:
    repository = tmp_path / "repository"
    docker = repository / "docker"
    docker.mkdir(parents=True)
    script = docker / "ci-prepare-smoke.sh"
    script.write_bytes((ROOT / "docker/ci-prepare-smoke.sh").read_bytes())
    script.chmod(0o755)
    assets = tmp_path / "assets"
    assets.mkdir()
    dump = b"PGDMP reviewed direct archive\n"
    bundle = b"reviewed legacy tar wrapper\n"
    manifest = b'{"corpus_release_id":"2026-08-30-r1"}\n'
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    for name, content in (
        ("corpus.dump", dump),
        ("manifest.json", manifest),
        ("SHA256SUMS", sums),
        ("corpus-bundle.tar.gz", bundle),
    ):
        (assets / name).write_bytes(content)
    data = (
        {
            "release_tag": "corpus-data-2026-08-30-r1",
            "digest": f"sha256:{hashlib.sha256(bundle).hexdigest()}",
        }
        if mode == "legacy"
        else {
            "release_tag": "corpus-data-2026-08-30-r1",
            "asset_name": "corpus.dump",
            "digest": f"sha256:{dump_digest}",
            "manifest_digest": f"sha256:{manifest_digest}",
            "checksums_digest": f"sha256:{hashlib.sha256(sums).hexdigest()}",
        }
    )
    (repository / "container-release.json").write_text(json.dumps({"data": data}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/python3\n"
        "import os, pathlib, shutil, sys\n"
        "target = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "source = pathlib.Path(os.environ['FAKE_ASSET_DIR']) / sys.argv[-1].rsplit('/', 1)[-1]\n"
        "shutil.copyfile(source, target)\n"
    )
    fake_curl.chmod(0o755)
    fixture_dir = tmp_path / "fixture"
    env_file = tmp_path / "smoke.env"

    result = subprocess.run(  # noqa: S603 - repository smoke shell is the test subject
        ["/bin/bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "FAKE_ASSET_DIR": str(assets),
            "GF_SMOKE_FIXTURE_DIR": str(fixture_dir),
            "GF_SMOKE_ENV_FILE": str(env_file),
            "GITHUB_REPOSITORY": "berntpopp/genereviews-link",
        },
    )

    assert result.returncode == 0, result.stderr
    if mode == "legacy":
        assert (fixture_dir / "corpus-seed/corpus-bundle.tar.gz").read_bytes() == bundle
        assert f"CORPUS_BUNDLE_SHA256={hashlib.sha256(bundle).hexdigest()}" in env_file.read_text()
    else:
        assert (fixture_dir / "corpus-seed/corpus.dump").read_bytes() == dump
        assert f"CORPUS_DUMP_SHA256={dump_digest}" in env_file.read_text()
        assert "CORPUS_RELEASE_TAG=corpus-data-2026-08-30-r1" in env_file.read_text()


def test_direct_seed_round_trip_binds_dump_manifest_and_checksum_identity(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    dump = b"PGDMP reviewed data-only archive\n"
    manifest = b'{"corpus_release_id":"2026-08-30-r1"}\n'
    sums = (
        f"{hashlib.sha256(dump).hexdigest()}  corpus.dump\n"
        f"{hashlib.sha256(manifest).hexdigest()}  manifest.json\n"
    ).encode()
    (seed / "corpus.dump").write_bytes(dump)
    (seed / "manifest.json").write_bytes(manifest)
    (seed / "SHA256SUMS").write_bytes(sums)

    restored = extract_bundle(
        seed,
        tmp_path / "restore",
        expected_sha256=hashlib.sha256(dump).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        expected_checksums_sha256=hashlib.sha256(sums).hexdigest(),
    )

    assert restored.dump.read_bytes() == dump
    assert restored.dump_sha256 == hashlib.sha256(dump).hexdigest()
    assert restored.manifest == {"corpus_release_id": "2026-08-30-r1"}


def test_direct_seed_streams_large_dump_with_bounded_python_memory(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    chunk = b"x" * (1024 * 1024)
    dump = seed / "corpus.dump"
    dump_hasher = hashlib.sha256()
    with dump.open("wb") as handle:
        for _ in range(12):
            handle.write(chunk)
            dump_hasher.update(chunk)
    manifest = b'{"corpus_release_id":"2026-08-30-r1"}\n'
    (seed / "manifest.json").write_bytes(manifest)
    dump_digest = dump_hasher.hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    (seed / "SHA256SUMS").write_bytes(sums)
    del chunk

    tracemalloc.start()
    extract_bundle(
        seed,
        tmp_path / "restore",
        expected_sha256=dump_digest,
        expected_manifest_sha256=manifest_digest,
        expected_checksums_sha256=hashlib.sha256(sums).hexdigest(),
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < 6 * 1024 * 1024


def test_direct_seed_rejects_untracked_assets(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    for name in ("corpus.dump", "manifest.json", "SHA256SUMS", "unexpected.txt"):
        (seed / name).write_bytes(b"x")

    with pytest.raises(ArchivePolicyError, match="exactly"):
        extract_bundle(
            seed,
            tmp_path / "restore",
            expected_sha256="1" * 64,
            expected_manifest_sha256="2" * 64,
            expected_checksums_sha256="3" * 64,
        )


def test_direct_seed_maps_malformed_manifest_to_archive_policy_error(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    dump = b"PGDMP reviewed data-only archive\n"
    manifest = b"\xff"
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    (seed / "corpus.dump").write_bytes(dump)
    (seed / "manifest.json").write_bytes(manifest)
    (seed / "SHA256SUMS").write_bytes(sums)

    with pytest.raises(ArchivePolicyError, match="valid JSON"):
        extract_bundle(
            seed,
            tmp_path / "restore",
            expected_sha256=dump_digest,
            expected_manifest_sha256=manifest_digest,
            expected_checksums_sha256=hashlib.sha256(sums).hexdigest(),
        )


def test_release_image_allowlist_contains_current_embedding_identity_migration() -> None:
    release = json.loads((ROOT / "container-release.json").read_bytes())

    assert (
        "opt/venv/lib/python3.12/site-packages/genereview_link/db/migrations/data/"
        "0007_embedding_run_identity.sql"
    ) in release["data"]["image_allowlist"]


@pytest.mark.parametrize(
    "uri_prefix",
    ["", "file:"],
)
def test_rights_record_rejects_nontransferable_filesystem_references(
    tmp_path: Path, uri_prefix: str
) -> None:
    document = tmp_path / "reviewed-rights-document"
    document.write_bytes(b"reviewed terms and evidence\n")
    uri = f"{uri_prefix}{document}"
    object_id = "1" * 64
    record = {
        "artifact_sha256": "2" * 64,
        "object_id": object_id,
        "decision": "affirmative",
        "approval_kind": "repository-owner redistribution determination",
        "upstream_approval": False,
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": "Bernt Popp / repository owner",
        "authorization_uri": "https://github.com/berntpopp/genereviews-link/issues/27",
        "decision_time": "2026-08-30T12:00:00Z",
        "terms_uri": uri,
        "terms_sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        "terms_version": "2026-08",
        "terms_source_uri": "https://www.genereviews.org/",
        "permitted_asset_use": (
            "immutable GeneReviews research corpus artifact for noncommercial research purposes "
            "only; no further modifications"
        ),
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": uri,
        "evidence_sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        "source_sha256": "3" * 64,
        "corpus_release_id": "2026-08-30-r1",
    }
    record["rights_record_sha256"] = hashlib.sha256(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    rights = tmp_path / "rights-record.json"
    rights.write_text(json.dumps(record))

    with pytest.raises(RightsError, match="bundle"):
        verify_rights_record(rights, object_id)


@pytest.mark.asyncio
async def test_ingest_records_immutable_identity_before_staging_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fail_record(*args: object, **kwargs: object) -> str:
        del args, kwargs
        calls.append("record")
        raise RuntimeError("record rejected")

    async def prepare(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("staging")

    monkeypatch.setattr(pipeline, "record_corpus_version_start", fail_record)
    monkeypatch.setattr(pipeline, "prepare_staging", prepare)
    monkeypatch.setattr(
        pipeline,
        "load_sidedata",
        lambda _path: SimpleNamespace(short_name_by_nbk={"NBK1": "one"}),
    )
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"retained")

    with pytest.raises(RuntimeError, match="record rejected"):
        await pipeline._ingest_files(
            object(),  # type: ignore[arg-type]
            listing=SimpleNamespace(last_updated="2026-08-31"),
            tarball=archive,
            sidedata_dir=tmp_path,
            tarball_sha256="1" * 64,
            side_data_identity={},
            source_capture={"chapter_ids": ["NBK1"]},
        )

    assert calls == ["record"]


@pytest.mark.asyncio
async def test_mutating_ingest_rejects_live_unretained_sources() -> None:
    with pytest.raises(ValueError, match="retained offline"):
        await pipeline.run_full_ingest(object())  # type: ignore[arg-type]
