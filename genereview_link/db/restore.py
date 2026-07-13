"""No-egress restore of an immutable, data-only corpus artifact.

The corpus is an *immutable reference dataset*, not a source of schema or code. This
module is the trust boundary between a downloaded archive and the database:

* the artifact must be a PostgreSQL **custom-format** archive (``PGDMP`` magic) --
  a downloaded plain-SQL script is arbitrary code and is rejected outright;
* every archive TOC entry must be **TABLE DATA** (or ``SEQUENCE SET``) for one of the
  exactly-named corpus tables. A ``SCHEMA``, ``FUNCTION``, ``TRIGGER``, ``EXTENSION``,
  ``ACL`` or any other entry is rejected, so the artifact can never create objects,
  execute code, or grant rights;
* the schema itself comes **only** from the reviewed in-repo migrations, which the
  caller applies before restoring;
* the restore runs as a **non-superuser** under
  ``--no-owner --no-privileges --single-transaction --exit-on-error``, so a partial or
  unexpected archive rolls back to nothing rather than leaving a half-loaded corpus.

The restoring container is started with no route off the internal network, so nothing
here may fetch anything: the artifact is already on disk, read-only, and is verified
against a digest committed in the repository before it is opened.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote

__all__ = [
    "ALLOWED_ENTRY_TYPES",
    "CORPUS_TABLES",
    "ArchivePolicyError",
    "CorpusBundle",
    "assert_data_only_archive",
    "ensure_restore_role",
    "extract_bundle",
    "read_archive_entries",
    "restore_data_only",
    "sha256_file",
]

#: The only TOC entry types an immutable data artifact may carry. Everything else --
#: SCHEMA, TABLE, INDEX, CONSTRAINT, FK CONSTRAINT, EXTENSION, FUNCTION, TRIGGER, ACL,
#: COMMENT, LARGE OBJECT -- is DDL or code and must come from reviewed migrations.
ALLOWED_ENTRY_TYPES = ("TABLE DATA", "SEQUENCE SET")

#: The exact tables the corpus artifact is allowed to populate.
CORPUS_TABLES = frozenset(
    {
        "genereview.genereview_chapters",
        "genereview.genereview_passages",
        "genereview.genereview_embeddings_bge384",
        "public.genereview_corpus_version",
    }
)

#: PostgreSQL custom-format archive magic. A plain-SQL dump does not have it.
_CUSTOM_FORMAT_MAGIC = b"PGDMP"

#: `pg_restore --list` entry: "<dumpId>; <catalogId> <oid> <DESC> <schema> <name> <owner>".
_ENTRY = re.compile(r"^\d+; \d+ \d+ (?P<rest>.+)$")

_MAX_BUNDLE_MEMBERS = 32
_MAX_MEMBER_BYTES = 4 * 1024**3
_SAFE_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SAFE_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _pg_restore() -> str:
    """Resolve pg_restore to an absolute path, failing closed if the image lacks it."""
    resolved = shutil.which("pg_restore")
    if resolved is None:
        raise ArchivePolicyError("pg_restore is not available in this image")
    return resolved


class ArchivePolicyError(RuntimeError):
    """The artifact is not an immutable, data-only corpus archive."""


@dataclass(frozen=True)
class CorpusBundle:
    """An extracted, checksum-verified corpus bundle."""

    root: Path
    dump: Path
    manifest: dict[str, object]

    @property
    def corpus_version(self) -> str:
        return str(self.manifest.get("corpus_version", ""))


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(handle: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def extract_bundle(archive: Path, destination: Path, *, expected_sha256: str) -> CorpusBundle:
    """Verify and expand the corpus bundle into ``destination``.

    The committed digest is the trust root: the bytes are proven BEFORE the archive is
    opened, so a substituted or truncated artifact never reaches the tar parser.

    Raises:
        ArchivePolicyError: the digest, member set, or per-member checksums do not match.
    """
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ArchivePolicyError("an exact 64-character corpus bundle SHA-256 is required")
    actual = sha256_file(archive)
    if actual != expected_sha256.lower():
        raise ArchivePolicyError("corpus bundle digest does not match the reviewed identity")

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        manifest_file = tar.extractfile(tar.getmember("manifest.json"))
        if manifest_file is None:
            raise ArchivePolicyError("manifest.json is not a regular file")
        manifest = json.loads(manifest_file.read())
        checksums = manifest.get("checksums")
        if not isinstance(checksums, dict) or not checksums:
            raise ArchivePolicyError("the bundle manifest declares no member checksums")

        expected_members = {"manifest.json", *checksums}
        members = tar.getmembers()
        if len(members) > _MAX_BUNDLE_MEMBERS:
            raise ArchivePolicyError("the bundle declares too many members")
        seen: set[str] = set()
        for member in members:
            if not _SAFE_MEMBER.fullmatch(member.name) or ".." in member.name.split("/"):
                raise ArchivePolicyError(f"unsafe tar member name: {member.name}")
            if member.isdir():
                # A plain directory entry carries no content. It is still only allowed to
                # be a parent of a declared member, so it can never create a stray path.
                if not any(name.startswith(f"{member.name}/") for name in expected_members):
                    raise ArchivePolicyError(f"unexpected tar member: {member.name}")
                continue
            if member.name in seen:
                raise ArchivePolicyError(f"duplicate tar member: {member.name}")
            seen.add(member.name)
            if member.name not in expected_members:
                raise ArchivePolicyError(f"unexpected tar member: {member.name}")
            if not member.isfile():
                # Links, devices and FIFOs are never content.
                raise ArchivePolicyError(f"tar member is not a regular file: {member.name}")
            if member.size > _MAX_MEMBER_BYTES:
                raise ArchivePolicyError(f"tar member exceeds the size ceiling: {member.name}")
        if seen != expected_members:
            raise ArchivePolicyError("the bundle does not contain exactly its declared members")

        for name, expected in checksums.items():
            handle = tar.extractfile(tar.getmember(name))
            if handle is None:
                raise ArchivePolicyError(f"manifest member is not a file: {name}")
            if _sha256_stream(handle) != expected:
                raise ArchivePolicyError(f"manifest checksum mismatch on {name}")

        for name in sorted(expected_members):
            tar.extract(tar.getmember(name), path=str(destination), filter="data")

    dump = destination / "corpus.dump"
    if not dump.is_file():
        raise ArchivePolicyError("the bundle carries no corpus.dump archive")
    return CorpusBundle(root=destination, dump=dump, manifest=manifest)


def read_archive_entries(dump: Path) -> list[str]:
    """Return the archive's TOC entries, proving it is a custom-format archive first.

    Raises:
        ArchivePolicyError: the file is not a PostgreSQL custom-format archive, or its
            table of contents cannot be read.
    """
    with dump.open("rb") as handle:
        if handle.read(len(_CUSTOM_FORMAT_MAGIC)) != _CUSTOM_FORMAT_MAGIC:
            raise ArchivePolicyError(
                "corpus artifact is not a PostgreSQL custom-format archive; a plain-SQL "
                "script is executable content and is never restored"
            )
    listed = subprocess.run(  # noqa: S603 - explicit absolute argv, never a shell
        [_pg_restore(), "--list", str(dump)],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        raise ArchivePolicyError("corpus archive table of contents could not be read")
    return [
        line
        for line in listed.stdout.splitlines()
        if line and not line.startswith(";") and not line.isspace()
    ]


def assert_data_only_archive(entries: list[str]) -> None:
    """Reject every TOC entry that is not data for an exactly-named corpus table.

    This is an allowlist: an entry that does not parse, or whose type or target is not
    explicitly permitted, is rejected. A schema-bearing or code-bearing archive can
    therefore never be restored, whatever the publisher put in it.

    Raises:
        ArchivePolicyError: the archive carries a non-data or unknown entry.
    """
    if not entries:
        raise ArchivePolicyError("corpus archive declares no data entries")
    for line in entries:
        match = _ENTRY.match(line)
        if match is None:
            raise ArchivePolicyError("corpus archive carries an unparseable catalog entry")
        rest = match.group("rest")
        entry_type = next(
            (allowed for allowed in ALLOWED_ENTRY_TYPES if rest.startswith(f"{allowed} ")),
            None,
        )
        if entry_type is None:
            raise ArchivePolicyError(
                "corpus archive carries a non-data entry; schema, indexes, extensions and "
                "code come only from reviewed in-repo migrations"
            )
        fields = rest[len(entry_type) :].split()
        if len(fields) < 2:
            raise ArchivePolicyError("corpus archive carries an incomplete catalog entry")
        target = f"{fields[0]}.{fields[1]}"
        if target not in CORPUS_TABLES:
            raise ArchivePolicyError(f"corpus archive targets an unapproved table: {target}")


def restore_data_only(dump: Path, *, database_url: str) -> None:
    """Restore a verified data-only archive as an unprivileged role, or leave nothing.

    ``--single-transaction --exit-on-error`` makes the restore atomic: any unexpected
    entry, constraint violation, or permission failure rolls the whole load back, so the
    database is never left holding a partially restored corpus.

    Raises:
        ArchivePolicyError: the restore did not complete cleanly.
    """
    completed = subprocess.run(  # noqa: S603 - explicit absolute argv, never a shell
        [
            _pg_restore(),
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            "--dbname",
            database_url,
            str(dump),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchivePolicyError("corpus restore failed and was rolled back in full")


async def ensure_restore_role(pool: Any, role: str, restore_url: str) -> None:
    """Create the least-privileged role that may load the artifact, and nothing else.

    Reviewed migrations run as the database owner. The untrusted artifact is loaded by a
    role that is explicitly ``NOSUPERUSER`` / ``NOCREATEDB`` / ``NOCREATEROLE`` and holds
    write rights on the exact corpus tables only -- so even an entry that somehow slipped
    past the archive policy would have no rights to create objects, reach other databases,
    or execute server-side code.

    Every identifier and literal is quoted by PostgreSQL itself (``format`` with ``%I`` /
    ``%L``), so a configured role name can never be concatenated into SQL.
    """
    if not _SAFE_ROLE.fullmatch(role):
        raise ArchivePolicyError("the configured restore role name is not a plain identifier")
    password = _password_of(restore_url)
    async with pool.acquire() as connection:
        exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", role)
        if not exists:
            statement = await connection.fetchval(
                "select format('create role %I login nosuperuser nocreatedb nocreaterole', $1::text)",
                role,
            )
            await connection.execute(statement)
        if password:
            statement = await connection.fetchval(
                "select format('alter role %I with password %L', $1::text, $2::text)",
                role,
                password,
            )
            await connection.execute(statement)
        for schema in sorted({table.split(".", 1)[0] for table in CORPUS_TABLES}):
            statement = await connection.fetchval(
                "select format('grant usage on schema %I to %I', $1::text, $2::text)", schema, role
            )
            await connection.execute(statement)
        for table in sorted(CORPUS_TABLES):
            schema, name = table.split(".", 1)
            statement = await connection.fetchval(
                "select format('grant select, insert, update, delete on %I.%I to %I', $1::text, $2::text, $3::text)",
                schema,
                name,
                role,
            )
            await connection.execute(statement)


def _password_of(url: str) -> str:
    """Return the password embedded in a libpq URL, or an empty string."""
    userinfo = url.partition("://")[2].partition("@")[0]
    return unquote(userinfo.partition(":")[2])
