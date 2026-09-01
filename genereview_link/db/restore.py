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
import os
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote, urlsplit

from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed
from genereview_link.db.process_guard import BoundedProcessError, run_bounded_process

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
    "seed_identity_mode",
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
        "public.genereview_computation_runs",
    }
)

#: PostgreSQL custom-format archive magic. A plain-SQL dump does not have it.
_CUSTOM_FORMAT_MAGIC = b"PGDMP"

#: `pg_restore --list` entry: "<dumpId>; <catalogId> <oid> <DESC> <schema> <name> <owner>".
_ENTRY = re.compile(r"^\d+; \d+ \d+ (?P<rest>.+)$")

_MAX_BUNDLE_MEMBERS = 32
_MAX_MEMBER_BYTES = 4 * 1024**3
_MAX_MANIFEST_BYTES = 1 << 20
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


def validate_restore_endpoint(owner_url: str, restore_url: str, *, role: str) -> None:
    """Require the restricted URL to address the owner's exact database endpoint."""
    owner = urlsplit(owner_url)
    restore = urlsplit(restore_url)
    try:
        for parsed in (owner, restore):
            _ = parsed.port  # validate a possibly explicit port before endpoint comparison
        plain_urls = all(
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname is not None
            and parsed.path.startswith("/")
            and "/" not in unquote(parsed.path)[1:]
            and parsed.query == ""
            and parsed.fragment == ""
            for parsed in (owner, restore)
        )
    except ValueError as error:
        raise ArchivePolicyError("restore endpoints must be plain PostgreSQL URLs") from error
    if not plain_urls:
        raise ArchivePolicyError("restore endpoints must be plain PostgreSQL URLs")
    if restore.username != role:
        raise ArchivePolicyError("restore URL username must equal the configured restore role")
    owner_endpoint = (owner.hostname, owner.port or 5432, unquote(owner.path))
    restore_endpoint = (restore.hostname, restore.port or 5432, unquote(restore.path))
    if owner_endpoint != restore_endpoint:
        raise ArchivePolicyError("restore URL must use the same database endpoint as the owner URL")


@dataclass(frozen=True)
class CorpusBundle:
    """An extracted, checksum-verified corpus bundle."""

    root: Path
    dump: Path
    manifest: dict[str, object]
    dump_sha256: str

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


def seed_identity_mode(
    bundle_sha256: str,
    dump_sha256: str,
    manifest_sha256: str,
    checksums_sha256: str,
) -> str:
    """Classify an exact legacy or direct seed identity; partial configurations fail."""
    direct = (dump_sha256, manifest_sha256, checksums_sha256)
    if all(re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value) for value in direct):
        return "direct"
    if any(direct):
        raise ArchivePolicyError("direct corpus seed identity is incomplete")
    if re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", bundle_sha256):
        return "legacy"
    raise ArchivePolicyError("legacy corpus seed identity is incomplete")


def extract_bundle(
    archive: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_manifest_sha256: str | None = None,
    expected_checksums_sha256: str | None = None,
) -> CorpusBundle:
    """Verify and expand the corpus bundle into ``destination``.

    The committed digest is the trust root: the bytes are proven BEFORE the archive is
    opened, so a substituted or truncated artifact never reaches the tar parser.

    Raises:
        ArchivePolicyError: the digest, member set, or per-member checksums do not match.
    """
    if archive.is_dir() and not archive.is_symlink():
        try:
            direct = extract_direct_seed(
                archive,
                destination,
                expected_dump_sha256=expected_sha256,
                expected_manifest_sha256=expected_manifest_sha256,
                expected_checksums_sha256=expected_checksums_sha256,
            )
        except DirectSeedError as error:
            raise ArchivePolicyError(str(error)) from error
        return CorpusBundle(
            root=direct.root,
            dump=direct.dump,
            manifest=direct.manifest,
            dump_sha256=direct.dump_sha256,
        )
    normalized_sha256 = expected_sha256.removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_sha256):
        raise ArchivePolicyError("an exact 64-character corpus bundle SHA-256 is required")
    actual = sha256_file(archive)
    if actual != normalized_sha256:
        raise ArchivePolicyError("corpus bundle digest does not match the reviewed identity")

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > _MAX_BUNDLE_MEMBERS:
            raise ArchivePolicyError("the bundle declares too many members")
        manifest_members = [member for member in members if member.name == "manifest.json"]
        if len(manifest_members) != 1:
            raise ArchivePolicyError("the bundle must contain one manifest.json")
        manifest_member = manifest_members[0]
        if not manifest_member.isfile() or manifest_member.size > _MAX_MANIFEST_BYTES:
            raise ArchivePolicyError("manifest.json is not a regular file within its size ceiling")
        manifest_file = tar.extractfile(manifest_member)
        if manifest_file is None:
            raise ArchivePolicyError("manifest.json is not a regular file")
        try:
            manifest = json.loads(manifest_file.read(_MAX_MANIFEST_BYTES + 1))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ArchivePolicyError("manifest.json is not valid bounded JSON") from error
        checksums = manifest.get("checksums")
        if not isinstance(checksums, dict) or not checksums:
            raise ArchivePolicyError("the bundle manifest declares no member checksums")

        expected_members = {"manifest.json", *checksums}
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
    dump_digest = checksums.get("corpus.dump")
    if not isinstance(dump_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", dump_digest):
        raise ArchivePolicyError("the bundle manifest has no exact corpus.dump digest")
    return CorpusBundle(
        root=destination,
        dump=dump,
        manifest=manifest,
        dump_sha256=dump_digest,
    )


def read_archive_entries(dump: Path, *, file_descriptor: int | None = None) -> list[str]:
    """Return the archive's TOC entries, proving it is a custom-format archive first.

    Raises:
        ArchivePolicyError: the file is not a PostgreSQL custom-format archive, or its
            table of contents cannot be read.
    """
    if file_descriptor is None:
        with dump.open("rb") as handle:
            magic = handle.read(len(_CUSTOM_FORMAT_MAGIC))
        archive_path = dump
        inherited: tuple[int, ...] = ()
    else:
        magic = os.pread(file_descriptor, len(_CUSTOM_FORMAT_MAGIC), 0)
        archive_path = Path(f"/proc/self/fd/{file_descriptor}")
        inherited = (file_descriptor,)
    if magic != _CUSTOM_FORMAT_MAGIC:
        raise ArchivePolicyError(
            "corpus artifact is not a PostgreSQL custom-format archive; a plain-SQL "
            "script is executable content and is never restored"
        )
    try:
        listed = run_bounded_process(
            [_pg_restore(), "--list", str(archive_path)],
            pass_fds=inherited,
            timeout_seconds=60.0,
            max_output_bytes=4 * 1024 * 1024,
        )
    except BoundedProcessError as error:
        raise ArchivePolicyError("corpus archive table of contents exceeded its bounds") from error
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
    try:
        completed = run_bounded_process(
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
            timeout_seconds=60 * 60.0,
            max_output_bytes=4 * 1024 * 1024,
        )
    except BoundedProcessError as error:
        raise ArchivePolicyError("corpus restore exceeded its reviewed process bounds") from error
    if completed.returncode != 0:
        raise ArchivePolicyError("corpus restore failed and was rolled back in full")


async def ensure_restore_role(pool: Any, role: str, restore_url: str, *, owner_url: str) -> None:
    """Create the least-privileged role that may load the artifact, and nothing else.

    Reviewed migrations run as the database owner. The untrusted artifact is loaded by a
    role that is explicitly ``NOSUPERUSER`` / ``NOCREATEDB`` / ``NOCREATEROLE`` and holds
    insert rights on the exact corpus tables only -- so even an entry that somehow slipped
    past the archive policy would have no rights to create objects, reach other databases,
    or execute server-side code.

    Every identifier and literal is quoted by PostgreSQL itself (``format`` with ``%I`` /
    ``%L``), so a configured role name can never be concatenated into SQL.
    """
    if not _SAFE_ROLE.fullmatch(role):
        raise ArchivePolicyError("the configured restore role name is not a plain identifier")
    validate_restore_endpoint(owner_url, restore_url, role=role)
    password = _password_of(restore_url)
    async with pool.acquire() as connection:
        exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", role)
        if not exists:
            statement = await connection.fetchval(
                "select format('create role %I login nosuperuser nocreatedb nocreaterole', $1::text)",
                role,
            )
            await connection.execute(statement)
        statement = await connection.fetchval(
            "select format('alter role %I login nosuperuser nocreatedb nocreaterole "
            "noreplication nobypassrls noinherit', $1::text)",
            role,
        )
        await connection.execute(statement)
        memberships = await connection.fetch(
            "select parent.rolname from pg_auth_members membership "
            "join pg_roles member on member.oid = membership.member "
            "join pg_roles parent on parent.oid = membership.roleid "
            "where member.rolname = $1 order by parent.rolname",
            role,
        )
        for membership in memberships:
            statement = await connection.fetchval(
                "select format('revoke %I from %I', $1::text, $2::text)",
                membership["rolname"],
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
        unsafe_role = await connection.fetchval(
            "select rolsuper or rolcreatedb or rolcreaterole or rolreplication "
            "or rolbypassrls or rolinherit from pg_roles where rolname = $1",
            role,
        )
        remaining_memberships = await connection.fetchval(
            "select count(*) from pg_auth_members membership "
            "join pg_roles member on member.oid = membership.member where member.rolname = $1",
            role,
        )
        if unsafe_role or int(remaining_memberships or 0) != 0:
            raise ArchivePolicyError("restore role retains escalation attributes or memberships")
        for schema in sorted({table.split(".", 1)[0] for table in CORPUS_TABLES}):
            statement = await connection.fetchval(
                "select format('revoke all on schema %I from %I', $1::text, $2::text)",
                schema,
                role,
            )
            await connection.execute(statement)
            statement = await connection.fetchval(
                "select format('grant usage on schema %I to %I', $1::text, $2::text)", schema, role
            )
            await connection.execute(statement)
        for table in sorted(CORPUS_TABLES):
            schema, name = table.split(".", 1)
            statement = await connection.fetchval(
                "select format('revoke all on %I.%I from %I', $1::text, $2::text, $3::text)",
                schema,
                name,
                role,
            )
            await connection.execute(statement)
            statement = await connection.fetchval(
                "select format('grant insert on %I.%I to %I', $1::text, $2::text, $3::text)",
                schema,
                name,
                role,
            )
            await connection.execute(statement)


def _password_of(url: str) -> str:
    """Return the password embedded in a libpq URL, or an empty string."""
    userinfo = url.partition("://")[2].partition("@")[0]
    return unquote(userinfo.partition(":")[2])
