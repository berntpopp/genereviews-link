"""Unit tests for corpus bundle building (local files only, no network/DB)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from genereview_link.corpus.bundle import (
    BundleManifest,
    pg_dump_to,
    sha256_file,
    write_bundle,
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


def test_pg_dump_to_calls_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: list[list[str]] = []

    import subprocess

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        called_with.append(cmd)
        # create empty dump file so write_bundle won't fail
        dump_path = Path(cmd[3])
        dump_path.write_bytes(b"")
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    dump_path = tmp_path / "corpus.dump"
    pg_dump_to(dump_path, database_url="postgresql://user:pass@localhost/db")
    assert called_with[0][:2] == ["pg_dump", "-Fc"]
    assert called_with[0][4] == "postgresql://user:pass@localhost/db"
