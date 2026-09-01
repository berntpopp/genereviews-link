"""Unit tests for corpus bundle building (local files only, no network/DB)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from genereview_link.corpus.bundle import (
    BundleManifest,
    pg_dump_to,
    sha256_file,
    write_bundle,
    write_data_only_bundle,
)


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello world")
    digest = sha256_file(f)
    assert len(digest) == 64
    # actual sha256("hello world")
    import hashlib

    assert digest == hashlib.sha256(b"hello world").hexdigest()


def test_bundle_manifest_defaults() -> None:
    m = BundleManifest()
    assert m.manifest_version == "1"
    assert m.bundle_format == "tar.gz"
    assert m.embedding["dimension"] == 384
    assert m.postgres["major_version"] == "18"
    assert m.checksums == {}


def test_bundle_manifest_includes_release_provenance_fields() -> None:
    manifest = BundleManifest(
        corpus_release_id="2026-05-12-r1",
        app_git_sha="abc123",
        schema_migrations={"control": ["0001_base"], "data": ["genereview:0001_chapters"]},
        validation={"status": "passed", "smoke_queries": []},
    )

    payload = asdict(manifest)

    assert payload["corpus_release_id"] == "2026-05-12-r1"
    assert payload["app_git_sha"] == "abc123"
    assert payload["schema_migrations"]["control"] == ["0001_base"]
    assert payload["validation"]["status"] == "passed"


def test_write_bundle_creates_tar_and_sha(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sidedata = tmp_path / "sidedata"
    sidedata.mkdir()

    # create fake corpus.dump
    dump = work_dir / "corpus.dump"
    dump.write_bytes(b"fake pg_dump data")

    # create fake sidedata file
    (sidedata / "GRtitle_shortname_NBKid.txt").write_text("NBK1\tshort\ttitle\n")

    output = tmp_path / "bundle.tar.gz"
    m = BundleManifest(corpus_version="2026-01-01", chapter_count=1, created_by="test")
    result = write_bundle(work_dir=work_dir, output=output, manifest=m, sidedata_dir=sidedata)

    assert result == output
    assert output.exists()
    sha_file = output.with_suffix(output.suffix + ".sha256")
    assert sha_file.exists()

    sha_content = sha_file.read_text()
    assert "bundle.tar.gz" in sha_content


def test_write_bundle_manifest_checksums(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sidedata = tmp_path / "sidedata"
    sidedata.mkdir()

    dump = work_dir / "corpus.dump"
    dump.write_bytes(b"content")

    sd_file = sidedata / "test.txt"
    sd_file.write_text("sidedata content")

    output = tmp_path / "bundle.tar.gz"
    m = BundleManifest(corpus_version="2026-01-01")
    write_bundle(work_dir=work_dir, output=output, manifest=m, sidedata_dir=sidedata)

    # manifest.json should be inside the tarball
    import tarfile as tf_mod

    with tf_mod.open(output, "r:gz") as tar:
        members = {m.name for m in tar.getmembers()}
        assert "manifest.json" in members
        assert "corpus.dump" in members
        assert "sidedata/test.txt" in members

        mf = tar.extractfile("manifest.json")
        assert mf is not None
        data = json.loads(mf.read())

    assert "corpus.dump" in data["checksums"]
    assert "sidedata/test.txt" in data["checksums"]


def test_data_only_bundle_has_canonical_metadata_and_exact_checksum_set(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"data-only-pgdump")
    manifest = BundleManifest(corpus_release_id="2026-08-30-r1", created_at="volatile")

    result = write_data_only_bundle(work_dir=work, output=tmp_path / "release", manifest=manifest)

    assert result == tmp_path / "release"
    assert {path.name for path in result.iterdir()} == {
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
    }
    metadata = json.loads((result / "manifest.json").read_text())
    assert "created_at" not in metadata
    assert metadata["bundle_format"] == "postgresql-custom-data-only"
    assert metadata["checksums"] == {"corpus.dump": hashlib.sha256(b"data-only-pgdump").hexdigest()}
    assert (result / "SHA256SUMS").read_text() == (
        f"{metadata['checksums']['corpus.dump']}  corpus.dump\n"
        f"{hashlib.sha256((result / 'manifest.json').read_bytes()).hexdigest()}  manifest.json\n"
    )


def test_pg_dump_to_calls_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        called_with.append(cmd)
        if cmd[-2:] == ["pg_dump", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pg_dump (PostgreSQL) 18.4\n", "")
        if cmd[-1] == "show server_version_num":
            return subprocess.CompletedProcess(cmd, 0, "180004\n", "")
        dump_path.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dump_path = tmp_path / "corpus.dump"
    pg_dump_to(dump_path, database_url="postgresql://user:pass@localhost/db")
    dump_command = called_with[2]
    assert dump_command[0:2] == ["docker", "run"]
    assert dump_command[-1] == "postgresql://user:pass@localhost/db"
    assert "--schema" not in dump_command
    assert "--extension" not in dump_command
    selected = [
        dump_command[index + 1]
        for index, argument in enumerate(dump_command)
        if argument == "--table"
    ]
    assert selected == [
        "genereview.genereview_chapters",
        "genereview.genereview_embeddings_bge384",
        "genereview.genereview_passages",
        "public.genereview_corpus_version",
        "public.genereview_computation_runs",
    ]
