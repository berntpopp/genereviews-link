"""Regressions for the final immutable GeneReviews publication closure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pytest

import genereview_link.corpus.pipeline as pipeline
from genereview_link.db.restore import ArchivePolicyError, extract_bundle

ROOT = Path(__file__).resolve().parents[2]


def test_production_image_copies_pg18_clients_without_mutable_pgdg_repository() -> None:
    dockerfile = (ROOT / "docker/Dockerfile").read_text()

    assert "apt.postgresql.org" not in dockerfile
    assert "COPY --from=pg18-client /usr/lib/postgresql/18/bin" in dockerfile
    assert "COPY --from=pg18-client /usr/share/postgresql/18" in dockerfile
    assert "COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libpq.so.5" in dockerfile


def test_verifier_binds_every_restore_input_without_claiming_an_attestation() -> None:
    """Nothing about the published bytes is taken on trust, and nothing is signed.

    The corpus is built on the maintainer's workstation, so there is no CI build to
    attest and the verifier must never pretend otherwise. What it does instead is prove
    all three published assets from scratch -- the checksum file against the bytes, the
    manifest against reviewed code -- before a single row is restored.
    """
    verifier = (ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text()

    assert not (ROOT / ".github/workflows/corpus-data-release.yml").exists()
    assert "gh attestation verify" not in verifier
    assert "attest-build-provenance" not in verifier
    assert "attestations:" not in verifier
    assert "id-token:" not in verifier
    for name in ("corpus.dump", "manifest.json", "SHA256SUMS"):
        assert f"--pattern {name}" in verifier
    assert "sha256sum -c SHA256SUMS" in verifier
    assert "verify_data_only_bundle" in verifier
    assert "build_provenance" in verifier and "rights_notice" in verifier


def test_production_seed_contract_preserves_current_pin_and_supports_direct_assets() -> None:
    compose = (ROOT / "docker/docker-compose.yml").read_text()
    smoke = (ROOT / "docker/ci-prepare-smoke.sh").read_text()
    config = json.loads((ROOT / "container-release.json").read_bytes())
    docs = (ROOT / "docs/deployment.md").read_text() + (ROOT / "docs/configuration.md").read_text()

    assert "CORPUS_SEED_PATH" in compose and "CORPUS_DUMP_SHA256" in compose
    assert "corpus-bundle.tar.gz)" in smoke and "corpus.dump)" in smoke
    assert config["data"]["release_tag"] == "corpus-data-2026-09-01-r1"
    assert config["data"]["digest"] == (
        "sha256:9e76402893b51ca6597ad434aef1feb71542a03c7566e43865081fbbff3fdca2"
    )
    # The fleet contract forbids unknown keys in `data`; which asset carries the digest
    # and the direct release's control-file digests are this repository's own pin.
    assert set(config["data"]) == {
        "mode",
        "release_tag",
        "schema_compatibility",
        "digest",
        "image_allowlist",
    }
    seed = json.loads((ROOT / "corpus-release.json").read_bytes())
    assert seed["release_tag"] == config["data"]["release_tag"]
    assert seed["digest"] == config["data"]["digest"]
    assert seed["asset_name"] == "corpus.dump"
    assert seed["manifest_digest"] == (
        "sha256:739a9e55636f2d4574fe5f714486b13ea1c8f7483d987f49f59f72170392585d"
    )
    assert seed["checksums_digest"] == (
        "sha256:f22ff0eaaba581d5f4ead33faaa3b67b06472dfceaed261c6b6fe6b6c9b2975e"
    )
    # Adopted in 5.2.4: the deployment now publishes the GeneFoundry runtime data identity
    # (v1) on /health, so the fleet controller can activate a new data release for it.
    assert config["data_identity_contract"] == "runtime-v1"
    assert "SHA256SUMS" in docs and "legacy" in docs


def _model_pins() -> dict[str, tuple[str, bytes]]:
    """Synthetic model members plus their digests, so no 127 MiB download is needed."""
    members = {}
    for name in ("model.onnx", "tokenizer.json"):
        payload = f"reviewed-{name}".encode()
        members[name] = (hashlib.sha256(payload).hexdigest(), payload)
    return members


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
    # The hook also stages the embedding model, reading its digest pins from the reviewed
    # identity module. That module is part of the contract under test, so the fake
    # repository must carry it rather than the hook degrading when it is absent.
    identity_dir = repository / "genereview_link/retrieval"
    identity_dir.mkdir(parents=True)
    _pins = _model_pins()
    identity_source = (ROOT / "genereview_link/retrieval/model_identity.py").read_text()
    identity_source = re.sub(
        r'BGE_ONNX_FILE_SHA256 = "[0-9a-f]{64}"',
        f'BGE_ONNX_FILE_SHA256 = "{_pins["model.onnx"][0]}"',
        identity_source,
    )
    identity_source = re.sub(
        r'"tokenizer\.json": "[0-9a-f]{64}"',
        f'"tokenizer.json": "{_pins["tokenizer.json"][0]}"',
        identity_source,
    )
    (identity_dir / "model_identity.py").write_text(identity_source)
    assets = tmp_path / "assets"
    assets.mkdir()
    dump = b"PGDMP reviewed direct archive\n"
    bundle = b"reviewed legacy tar wrapper\n"
    manifest = b'{"corpus_release_id":"2026-08-30-r1"}\n'
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    model_identity = _model_pins()
    for name, content in (
        ("corpus.dump", dump),
        ("manifest.json", manifest),
        ("SHA256SUMS", sums),
        ("corpus-bundle.tar.gz", bundle),
        ("model.onnx", model_identity["model.onnx"][1]),
        ("tokenizer.json", model_identity["tokenizer.json"][1]),
    ):
        (assets / name).write_bytes(content)
    if mode == "legacy":
        data = {
            "release_tag": "corpus-data-2026-08-30-r1",
            "digest": f"sha256:{hashlib.sha256(bundle).hexdigest()}",
        }
    else:
        data = {
            "release_tag": "corpus-data-2026-08-30-r1",
            "digest": f"sha256:{dump_digest}",
        }
        (repository / "corpus-release.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_tag": "corpus-data-2026-08-30-r1",
                    "asset_name": "corpus.dump",
                    "digest": f"sha256:{dump_digest}",
                    "manifest_digest": f"sha256:{manifest_digest}",
                    "checksums_digest": f"sha256:{hashlib.sha256(sums).hexdigest()}",
                }
            )
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
    # The model members are staged into the same seed directory the sidecar binds, and the
    # hook proves them against the in-repo pins before writing the smoke env.
    assert (fixture_dir / "corpus-seed/model/model.onnx").is_file()
    assert (fixture_dir / "corpus-seed/model/tokenizer.json").is_file()
    assert "MODEL_SEED_PATH=/seed/model" in env_file.read_text()
    assert "GENEREVIEW_EMBEDDING_PROVIDER=onnx" in env_file.read_text()
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
