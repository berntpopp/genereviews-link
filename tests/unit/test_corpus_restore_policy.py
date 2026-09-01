"""The corpus artifact is DATA. It may never carry schema, code, or rights.

These tests pin the trust boundary the no-egress restore sidecar enforces: the archive
must be a custom-format dump whose every catalog entry is table data for an exactly-named
corpus table. Schema, indexes, extensions and code come only from reviewed in-repo
migrations, so a publisher (or anyone who can serve the artifact) can never use the
corpus to create objects, execute code, or grant privileges.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

import genereview_link.db.restore as restore
from genereview_link.db.restore import (
    ArchivePolicyError,
    assert_data_only_archive,
    ensure_restore_role,
    extract_bundle,
    read_archive_entries,
)

# Real `pg_restore --list` lines from the data-only corpus archive.
DATA_ENTRIES = [
    "3448; 0 16714 TABLE DATA genereview genereview_chapters genereview",
    "3450; 0 16745 TABLE DATA genereview genereview_passages genereview",
    "3449; 0 16732 TABLE DATA genereview genereview_embeddings_bge384 genereview",
    "3451; 0 16783 TABLE DATA public genereview_corpus_version genereview",
]


def test_a_data_only_archive_is_accepted() -> None:
    assert_data_only_archive(DATA_ENTRIES)  # must not raise


@pytest.mark.parametrize(
    "entry",
    [
        # Every one of these was present in the LEGACY schema-bearing corpus dump.
        "5; 2615 16713 SCHEMA - genereview genereview",
        "216; 1259 16714 TABLE genereview genereview_chapters genereview",
        "3299; 1259 16800 INDEX genereview genereview_chapters_pkey genereview",
        "3301; 2606 16810 CONSTRAINT genereview genereview_passages_pkey genereview",
        "3302; 2606 16820 FK CONSTRAINT genereview genereview_passages_fk genereview",
        "2; 3079 16385 EXTENSION - vector -",
        "3448; 0 16714 COMMENT - SCHEMA genereview genereview",
        # Code-bearing entries that must never be restorable.
        "220; 1255 16900 FUNCTION genereview evil() genereview",
        "221; 1255 16901 PROCEDURE genereview evil() genereview",
        "222; 2620 16902 TRIGGER genereview t genereview",
        "223; 0 0 ACL - SCHEMA public postgres",
        "224; 1247 16903 TYPE genereview t genereview",
        "225; 0 16904 LARGE OBJECT - 16904 postgres",
    ],
)
def test_every_non_data_entry_is_rejected(entry: str) -> None:
    with pytest.raises(ArchivePolicyError, match="non-data entry"):
        assert_data_only_archive([*DATA_ENTRIES, entry])


def test_data_for_an_unapproved_table_is_rejected() -> None:
    intruder = "3452; 0 16999 TABLE DATA public pg_shadow_copy genereview"
    with pytest.raises(ArchivePolicyError, match="unapproved table"):
        assert_data_only_archive([*DATA_ENTRIES, intruder])


def test_an_unparseable_entry_is_rejected() -> None:
    with pytest.raises(ArchivePolicyError, match="unparseable"):
        assert_data_only_archive(["TABLE DATA genereview genereview_chapters"])


def test_an_empty_archive_is_rejected() -> None:
    with pytest.raises(ArchivePolicyError, match="no data entries"):
        assert_data_only_archive([])


@pytest.mark.asyncio
async def test_restore_role_is_reduced_to_insert_only_table_rights() -> None:
    formatted: list[str] = []

    class Connection:
        async def fetchval(self, query: str, *args: object) -> object:
            if query.startswith("select 1 from pg_roles"):
                return 1
            if query.startswith("select rolsuper"):
                return False
            if query.startswith("select count(*) from pg_auth_members"):
                return 0
            formatted.append(query)
            return f"statement-{len(formatted)}"

        async def fetch(self, query: str, *args: object) -> list[object]:
            return []

        async def execute(self, statement: str) -> None:
            assert statement.startswith("statement-")

    class Acquire:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *args: object) -> None:
            return None

    class Pool:
        def acquire(self) -> Any:
            return Acquire()

    await ensure_restore_role(
        Pool(),
        "genereview_restore",
        "postgresql://genereview_restore:secret@postgres/genereview",
        owner_url="postgresql://owner:secret@postgres/genereview",
    )

    scripts = "\n".join(formatted)
    assert (
        "alter role %I login nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit"
        in scripts
    )
    assert "revoke all on %I.%I from %I" in scripts
    assert "revoke all on schema %I from %I" in scripts
    assert "grant insert on %I.%I to %I" in scripts
    assert "grant select, insert, update, delete" not in scripts


def test_plain_sql_is_never_opened_as_an_archive(tmp_path: Path) -> None:
    """A downloaded .sql script is arbitrary code: reject it on the magic bytes."""
    script = tmp_path / "corpus.dump"
    script.write_bytes(b"CREATE EXTENSION plpython3u;\nDROP TABLE genereview_chapters;\n")
    with pytest.raises(ArchivePolicyError, match="custom-format"):
        read_archive_entries(script)


def test_archive_toc_consumes_the_already_open_descriptor_during_parent_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "source"
    replacement = tmp_path / "replacement"
    parent.mkdir()
    replacement.mkdir()
    dump = parent / "corpus.dump"
    dump.write_bytes(b"PGDMP-safe")
    (replacement / "corpus.dump").write_bytes(b"PGDMP-attacker")
    descriptor = os.open(dump, os.O_RDONLY | os.O_NOFOLLOW)
    parent.rename(tmp_path / "original")
    parent.symlink_to(replacement, target_is_directory=True)

    def inspect_descriptor(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["pass_fds"] == (descriptor,)
        assert Path(argv[-1]).read_bytes() == b"PGDMP-safe"
        return subprocess.CompletedProcess(argv, 0, stdout=DATA_ENTRIES[0] + "\n", stderr="")

    monkeypatch.setattr(restore.subprocess, "run", inspect_descriptor)
    try:
        entries = read_archive_entries(dump, file_descriptor=descriptor)
    finally:
        os.close(descriptor)

    assert entries == DATA_ENTRIES[:1]


# --- bundle extraction: the committed digest is the trust root -------------------------


def _bundle(tmp_path: Path, *, dump: bytes = b"PGDMP-fake", extra: str | None = None) -> Path:
    members: dict[str, bytes] = {"corpus.dump": dump}
    if extra is not None:
        members[extra] = b"payload"
    manifest = {
        "corpus_version": "2026-05-10-r6",
        "checksums": {name: hashlib.sha256(body).hexdigest() for name, body in members.items()},
    }
    archive = tmp_path / "corpus.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        body = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return archive


def test_bundle_with_the_reviewed_digest_is_extracted(tmp_path: Path) -> None:
    archive = _bundle(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    bundle = extract_bundle(archive, tmp_path / "out", expected_sha256=digest)
    assert bundle.dump.is_file()
    assert bundle.corpus_version == "2026-05-10-r6"


def test_a_substituted_bundle_never_reaches_the_tar_parser(tmp_path: Path) -> None:
    archive = _bundle(tmp_path)
    with pytest.raises(ArchivePolicyError, match="digest does not match"):
        extract_bundle(archive, tmp_path / "out", expected_sha256="0" * 64)


def test_an_unpinned_deployment_fails_closed(tmp_path: Path) -> None:
    archive = _bundle(tmp_path)
    with pytest.raises(ArchivePolicyError, match="exact 64-character"):
        extract_bundle(archive, tmp_path / "out", expected_sha256="")


def test_a_member_outside_the_manifest_is_rejected(tmp_path: Path) -> None:
    """A stowaway file cannot ride along even if the tar and digest are consistent."""
    archive = tmp_path / "corpus.tar.gz"
    manifest = {
        "corpus_version": "x",
        "checksums": {"corpus.dump": hashlib.sha256(b"PGDMP").hexdigest()},
    }
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in (
            ("manifest.json", json.dumps(manifest).encode()),
            ("corpus.dump", b"PGDMP"),
            ("stowaway.sh", b"rm -rf /"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ArchivePolicyError, match="unexpected tar member"):
        extract_bundle(archive, tmp_path / "out", expected_sha256=digest)


def test_a_directory_entry_may_only_parent_a_declared_member(tmp_path: Path) -> None:
    """The real bundle carries a `sidedata/` directory entry; a stray one is still rejected."""
    archive = tmp_path / "corpus.tar.gz"
    manifest = {
        "corpus_version": "x",
        "checksums": {
            "corpus.dump": hashlib.sha256(b"PGDMP").hexdigest(),
            "sidedata/a.txt": hashlib.sha256(b"a").hexdigest(),
        },
    }
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in (
            ("manifest.json", json.dumps(manifest).encode()),
            ("corpus.dump", b"PGDMP"),
            ("sidedata/a.txt", b"a"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        for name in ("sidedata", "elsewhere"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ArchivePolicyError, match="unexpected tar member: elsewhere"):
        extract_bundle(archive, tmp_path / "out", expected_sha256=digest)


# --- properties inherited from the old in-app bundle bootstrap ---------------------------
#
# The server used to download and pg_restore a bundle itself. That path is gone (a serving
# process must have neither the egress nor the database rights to do it), but the tar
# hardening it carried still applies -- to the restore sidecar. These pin it there.


def _tar(tmp_path: Path, entries: list[tuple[str, bytes]], checksums: dict[str, str]) -> Path:
    archive = tmp_path / "corpus.tar.gz"
    manifest = {"corpus_version": "x", "checksums": checksums}
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in [("manifest.json", json.dumps(manifest).encode()), *entries]:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return archive


def test_a_duplicate_member_is_rejected(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path,
        [("corpus.dump", b"PGDMP"), ("corpus.dump", b"PGDMP")],
        {"corpus.dump": hashlib.sha256(b"PGDMP").hexdigest()},
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ArchivePolicyError, match="duplicate tar member"):
        extract_bundle(archive, tmp_path / "out", expected_sha256=digest)


def test_a_path_traversal_member_is_rejected(tmp_path: Path) -> None:
    payload = b"PGDMP"
    archive = _tar(
        tmp_path,
        [("corpus.dump", payload), ("../../etc/evil", payload)],
        {
            "corpus.dump": hashlib.sha256(payload).hexdigest(),
            "../../etc/evil": hashlib.sha256(payload).hexdigest(),
        },
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ArchivePolicyError, match="unsafe tar member name"):
        extract_bundle(archive, tmp_path / "out", expected_sha256=digest)


def test_a_forged_manifest_is_caught_before_anything_is_extracted(tmp_path: Path) -> None:
    """The manifest claims a checksum the member does not have: nothing may be written."""
    archive = _tar(
        tmp_path,
        [("corpus.dump", b"tampered")],
        {"corpus.dump": hashlib.sha256(b"PGDMP").hexdigest()},
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / "out"
    with pytest.raises(ArchivePolicyError, match="manifest checksum mismatch"):
        extract_bundle(archive, destination, expected_sha256=digest)
    assert not (destination / "corpus.dump").exists()
