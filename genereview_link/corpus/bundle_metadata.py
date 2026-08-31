"""Metadata helpers for corpus bundle releases."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import asyncpg

RELEASE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-r[1-9]\d*$")
GIT_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@dataclass(frozen=True)
class BundleDatabaseFacts:
    """Exact database and source facts bound into a data-only bundle."""

    corpus_version: str
    tarball_source_sha256: str
    tarball_last_updated: str
    tarball_size_bytes: int
    chapter_count: int
    passage_count: int
    embedding_count: int
    hnsw_exists: bool
    schema_migrations: dict[str, list[str]]
    postgres_major: str
    pgvector_version: str

    @property
    def source(self) -> dict[str, object]:
        return {
            "file_list_etag": self.tarball_last_updated,
            "tarball_size_bytes": self.tarball_size_bytes,
        }

    @property
    def postgres(self) -> dict[str, object]:
        return {
            "major_version": self.postgres_major,
            "pgvector_version": self.pgvector_version,
        }


def _validated_git_revision(value: str) -> str:
    revision = value.strip()
    if not GIT_REVISION_RE.fullmatch(revision):
        raise ValueError("application Git revision must be an exact lowercase object ID")
    return revision


def resolve_app_git_sha(
    *,
    env: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> str:
    """Resolve the exact clean source revision used by the bundle producer."""
    current_env = os.environ if env is None else env
    if github_sha := current_env.get("GITHUB_SHA"):
        return _validated_git_revision(github_sha)

    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to bind a local bundle to its source revision")
    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        status = subprocess.run(  # noqa: S603 - absolute executable and fixed arguments
            [git, "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status.stdout:
            raise RuntimeError("local bundle source worktree must be clean")
        resolved = subprocess.run(  # noqa: S603 - absolute executable and fixed arguments
            [git, "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("could not resolve the local bundle source revision") from error
    return _validated_git_revision(resolved.stdout)


async def collect_database_facts(pool: asyncpg.Pool) -> BundleDatabaseFacts | None:
    """Collect complete, fail-closed provenance for the one active corpus."""
    row = await pool.fetchrow(
        """
        select version, file_list_etag, tarball_sha256, tarball_size_bytes, chapter_count
          from public.genereview_corpus_version
         where is_active and ingest_status = 'completed'
        """
    )
    if row is None:
        return None

    migration_rows = await pool.fetch(
        """
        select namespace, version
          from public.schema_migrations
         order by namespace, version
        """
    )
    migrations: dict[str, list[str]] = {"control": [], "data": []}
    for migration in migration_rows:
        namespace = str(migration["namespace"])
        if namespace not in migrations:
            raise ValueError(f"unexpected migration namespace: {namespace}")
        migrations[namespace].append(str(migration["version"]))

    passage_count = int(
        await pool.fetchval('select count(*) from "genereview".genereview_passages') or 0
    )
    embedding_count = int(
        await pool.fetchval(
            """
            select count(*)
              from "genereview".genereview_embeddings_bge384
             where model_name = 'BAAI/bge-small-en-v1.5'
            """
        )
        or 0
    )
    hnsw_exists = bool(
        await pool.fetchval(
            """
            select exists (
              select 1 from pg_indexes
               where schemaname = 'genereview'
                 and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
            )
            """
        )
    )
    server_version = str(await pool.fetchval("select current_setting('server_version_num')"))
    pgvector_version = str(
        await pool.fetchval("select extversion from pg_extension where extname = 'vector'") or ""
    )

    source_sha256 = str(row["tarball_sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("active corpus source SHA-256 is invalid")
    source_size = int(row["tarball_size_bytes"] or 0)
    if source_size <= 0:
        raise ValueError("active corpus source size is invalid")
    if not server_version.isdigit() or len(server_version) < 5:
        raise ValueError("PostgreSQL server version is invalid")
    if not pgvector_version:
        raise ValueError("pgvector extension version is missing")

    return BundleDatabaseFacts(
        corpus_version=str(row["version"]),
        tarball_source_sha256=source_sha256,
        tarball_last_updated=str(row["file_list_etag"]),
        tarball_size_bytes=source_size,
        chapter_count=int(row["chapter_count"] or 0),
        passage_count=passage_count,
        embedding_count=embedding_count,
        hnsw_exists=hnsw_exists,
        schema_migrations=migrations,
        postgres_major=str(int(server_version) // 10_000),
        pgvector_version=pgvector_version,
    )


def validate_release_id(release_id: str) -> str:
    """Validate the release id component used in corpus release tags."""
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError("release_id must use YYYY-MM-DD-rN, for example 2026-05-12-r1")
    return release_id


def asset_name_for_release(
    release_id: str,
    *,
    model_slug: str,
    postgres_major: str,
    pgvector_version: str,
) -> str:
    """Return the canonical tarball asset name for a corpus release."""
    validated = validate_release_id(release_id)
    return f"genereview-corpus-{validated}-{model_slug}-{postgres_major}-{pgvector_version}.tar.gz"
