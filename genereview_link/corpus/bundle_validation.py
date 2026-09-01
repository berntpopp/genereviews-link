"""Validation helpers for publishable corpus bundles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import asyncpg

from genereview_link.corpus.schema_identity import (
    EXPECTED_CONTROL_MIGRATIONS,
    EXPECTED_DATA_MIGRATIONS,
)


class _ConnectionPool:
    """Small pool facade used to validate inside a caller-owned transaction."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    def acquire(self) -> _ConnectionAcquire:
        return _ConnectionAcquire(self.connection)


class _ConnectionAcquire:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> asyncpg.Connection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(value: str) -> str:
    if not IDENT_RE.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return f'"{value}"'


@dataclass(frozen=True)
class BundleValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_manifest(self) -> dict[str, Any]:
        return {
            "status": "passed" if self.ok else "failed",
            "errors": self.errors,
            "warnings": self.warnings,
            "smoke_queries": [],
        }


async def validate_database_ready(
    pool: asyncpg.Pool,
    *,
    schema: str = "genereview",
    min_chapters: int = 880,
    min_passages: int = 40_000,
    embedding_table: str = "genereview_embeddings_bge384",
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> BundleValidationResult:
    """Validate that a database contains a complete publishable corpus."""
    errors: list[str] = []
    warnings: list[str] = []
    quoted_schema = _quote_ident(schema)
    quoted_embedding_table = _quote_ident(embedding_table)
    async with pool.acquire() as conn:
        migration_rows = await conn.fetch(
            "select namespace, version from public.schema_migrations "
            "where namespace in ('control', 'data')"
        )
        applied_control = {
            str(row["version"]) for row in migration_rows if row["namespace"] == "control"
        }
        applied_data = {str(row["version"]) for row in migration_rows if row["namespace"] == "data"}
        if applied_control != EXPECTED_CONTROL_MIGRATIONS:
            errors.append("control schema migrations do not match the reviewed migration set")
        if applied_data != EXPECTED_DATA_MIGRATIONS:
            errors.append("data schema migrations do not match the reviewed migration set")
        actual_pg_major = str(
            int(await conn.fetchval("select current_setting('server_version_num')")) // 10_000
        )
        actual_pgvector = str(
            await conn.fetchval("select extversion from pg_extension where extname = 'vector'")
            or ""
        )
        if actual_pg_major != "18":
            errors.append(f"PostgreSQL major {actual_pg_major} is not the reviewed major 18")
        if actual_pgvector != "0.8.2":
            errors.append(f"pgvector {actual_pgvector!r} is not the reviewed version 0.8.2")
        active_version = await conn.fetchval(
            "select version from public.genereview_corpus_version where is_active"
        )
        if not active_version:
            errors.append("no active corpus version")

        chapter_count = int(
            await conn.fetchval(
                f"select count(*) from {quoted_schema}.genereview_chapters"  # noqa: S608
            )
            or 0
        )
        passage_count = int(
            await conn.fetchval(
                f"select count(*) from {quoted_schema}.genereview_passages"  # noqa: S608
            )
            or 0
        )
        embedding_count = int(
            await conn.fetchval(
                f"select count(*) from {quoted_schema}.{quoted_embedding_table} "  # noqa: S608
                "where model_name = $1",
                model_name,
            )
            or 0
        )
        from genereview_link.retrieval.model_identity import BGE_MODEL_REVISION

        model_revision_matches = bool(
            await conn.fetchval(
                f"select count(*) > 0 and "  # noqa: S608
                f"count(*) filter (where model_revision = $2) = count(*) "
                f"from {quoted_schema}.{quoted_embedding_table} where model_name = $1",
                model_name,
                BGE_MODEL_REVISION,
            )
        )
        hnsw_exists = bool(
            await conn.fetchval(
                """
                select exists (
                  select 1 from pg_indexes
                   where schemaname = $1
                     and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
                )
                """,
                schema,
            )
        )
        active_embedding = await conn.fetchrow(
            """
            select table_name, model_name
              from public.genereview_active_embedding
             where id = 1
            """
        )

    if chapter_count < min_chapters:
        errors.append(f"chapter count {chapter_count} is below minimum {min_chapters}")
    if passage_count < min_passages:
        errors.append(f"passage count {passage_count} is below minimum {min_passages}")
    if embedding_count != passage_count:
        errors.append(
            f"embedding count {embedding_count} does not equal passage count {passage_count}"
        )
    if not model_revision_matches:
        errors.append("embeddings do not use the exact reviewed model revision")
    if not hnsw_exists:
        errors.append("HNSW index genereview_embeddings_bge384_hnsw_cosine is missing")
    if active_embedding is None:
        errors.append("public.genereview_active_embedding row is missing")
    elif (
        active_embedding["table_name"] != embedding_table
        or active_embedding["model_name"] != model_name
    ):
        errors.append(
            "public.genereview_active_embedding does not match bundled embedding table/model"
        )

    return BundleValidationResult(errors=errors, warnings=warnings)


async def validate_database_ready_from_connection(
    connection: asyncpg.Connection,
    *,
    schema: str = "genereview",
    min_chapters: int = 880,
    min_passages: int = 40_000,
    embedding_table: str = "genereview_embeddings_bge384",
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> BundleValidationResult:
    """Run validation on a caller-owned repeatable-read connection."""
    return await validate_database_ready(
        _ConnectionPool(connection),
        schema=schema,
        min_chapters=min_chapters,
        min_passages=min_passages,
        embedding_table=embedding_table,
        model_name=model_name,
    )
