"""Immutable database records for ingest and embedding computation runs."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import asyncpg

from genereview_link.corpus.jsonb import JsonbColumnError, json_object

_SERVER_VERSION = re.compile(r"^18[0-9]{4}$")


def resolve_app_git_sha() -> str:
    """Load build-only Git provenance code only when recording a new run."""
    from genereview_link.corpus.bundle_metadata import resolve_app_git_sha as resolve

    return resolve()


def collect_computation_provenance(*, app_git_sha: str) -> dict[str, object]:
    """Load model/build provenance code only when recording a new run."""
    from genereview_link.corpus.computation_provenance import (
        collect_computation_provenance as collect,
    )

    return collect(app_git_sha=app_git_sha)


def _database_identity(row: Any) -> dict[str, str]:
    server_version = str(row["server_version_num"])
    pgvector = str(row["pgvector"])
    if not _SERVER_VERSION.fullmatch(server_version) or pgvector != "0.8.2":
        raise RuntimeError("database runtime does not match the reviewed PostgreSQL 18 identity")
    return {
        "server_version_num": server_version,
        "server_major": str(int(server_version) // 10_000),
        "pgvector": pgvector,
    }


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def computation_run_id(
    *,
    phase: str,
    corpus_version: str,
    expected_row_count: int,
    provenance: dict[str, object],
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "phase": phase,
                "corpus_version": corpus_version,
                "expected_row_count": expected_row_count,
                "provenance": provenance,
            }
        )
    ).hexdigest()


async def begin_embedding_run(
    pool: asyncpg.Pool, *, expected_row_count: int
) -> tuple[str, dict[str, object]] | None:
    """Record immutable evidence before any active-corpus embedding row is written."""
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            select version, current_setting('server_version_num') as server_version_num,
                   (select extversion from pg_extension where extname = 'vector') as pgvector
              from public.genereview_corpus_version
             where is_active and ingest_status = 'completed'
            """
        )
    if row is None:
        return None
    app_git_sha = resolve_app_git_sha()
    provenance = collect_computation_provenance(app_git_sha=app_git_sha)
    exact = deepcopy(provenance)
    database = exact["database"]
    assert isinstance(database, dict)
    database.update(_database_identity(row))
    corpus_version = str(row["version"])
    run_id = computation_run_id(
        phase="embedding",
        corpus_version=corpus_version,
        expected_row_count=expected_row_count,
        provenance=exact,
    )
    recorded_at = datetime.now(UTC)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            """
            insert into public.genereview_computation_runs
                (run_id, corpus_version, phase, app_git_sha, provenance,
                 expected_row_count, recorded_at)
            values ($1, $2, 'embedding', $3, $4::jsonb, $5, $6)
            on conflict (run_id) do nothing
            """,
            run_id,
            corpus_version,
            app_git_sha,
            json.dumps(exact, sort_keys=True, separators=(",", ":")),
            expected_row_count,
            recorded_at,
        )
    return run_id, exact


async def record_ingest_run(
    connection: Any,
    *,
    corpus_version: str,
    source_capture: dict[str, object],
    expected_row_count: int,
) -> str:
    """Insert immutable ingest-time environment/source evidence."""
    database_row = await connection.fetchrow(
        """
        select current_setting('server_version_num') as server_version_num,
               (select extversion from pg_extension where extname = 'vector') as pgvector
        """
    )
    if database_row is None:
        raise RuntimeError("database runtime identity is unavailable")
    app_git_sha = resolve_app_git_sha()
    provenance = deepcopy(collect_computation_provenance(app_git_sha=app_git_sha))
    database = provenance["database"]
    if not isinstance(database, dict):
        raise RuntimeError("computation provenance database identity is invalid")
    database.update(_database_identity(database_row))
    provenance["source_capture"] = source_capture
    run_id = computation_run_id(
        phase="ingest",
        corpus_version=corpus_version,
        expected_row_count=expected_row_count,
        provenance=provenance,
    )
    await connection.execute(
        """
        insert into public.genereview_computation_runs
            (run_id, corpus_version, phase, app_git_sha, provenance,
             expected_row_count, recorded_at)
        values ($1, $2, 'ingest', $3, $4::jsonb, $5, $6)
        """,
        run_id,
        corpus_version,
        app_git_sha,
        json.dumps(provenance, sort_keys=True, separators=(",", ":")),
        expected_row_count,
        datetime.now(UTC),
    )
    await connection.execute(
        "update public.genereview_corpus_version set ingest_run_id = $1 where version = $2",
        run_id,
        corpus_version,
    )
    return run_id


async def complete_embedding_run(pool: asyncpg.Pool, *, run_id: str) -> None:
    """Bind the active corpus only after every embedding row uses the recorded run."""
    async with pool.acquire() as connection, connection.transaction():
        run = await connection.fetchrow(
            "select corpus_version, expected_row_count from public.genereview_computation_runs "
            "where run_id = $1 and phase = 'embedding'",
            run_id,
        )
        if run is None:
            raise ValueError("embedding computation run is missing")
        count = int(
            await connection.fetchval(
                "select count(*) from genereview.genereview_embeddings_bge384 "
                "where embedding_run_id = $1",
                run_id,
            )
            or 0
        )
        if count != int(run["expected_row_count"]):
            raise ValueError("embedding computation run does not cover every passage")
        result = await connection.execute(
            "update public.genereview_corpus_version set embedding_run_id = $1 "
            "where version = $2 and is_active",
            run_id,
            run["corpus_version"],
        )
        if result != "UPDATE 1":
            raise ValueError("embedding computation run is not tied to the active corpus")


async def load_active_computation(connection: Any) -> dict[str, object]:
    row = await connection.fetchrow(
        """
        select c.version, embedding.run_id, embedding.app_git_sha, embedding.provenance,
               embedding.expected_row_count,
               ingest.run_id as ingest_run_id, ingest.app_git_sha as ingest_app_git_sha,
               ingest.provenance as ingest_provenance,
               ingest.expected_row_count as ingest_expected_row_count,
               c.source_capture
          from public.genereview_corpus_version c
          join public.genereview_computation_runs embedding
            on embedding.run_id = c.embedding_run_id and embedding.phase = 'embedding'
           and embedding.corpus_version = c.version
          join public.genereview_computation_runs ingest
            on ingest.run_id = c.ingest_run_id and ingest.phase = 'ingest'
           and ingest.corpus_version = c.version
         where c.is_active and c.ingest_status = 'completed'
        """
    )
    if row is None:
        raise ValueError("active corpus has no complete immutable computation-run chain")
    try:
        provenance = json_object(row["provenance"], label="embedding provenance")
        ingest_provenance = json_object(row["ingest_provenance"], label="ingest provenance")
        source_capture = json_object(row["source_capture"], label="source capture")
    except JsonbColumnError as error:
        raise ValueError("active corpus has no complete immutable computation-run chain") from error
    if ingest_provenance.get("source_capture") != source_capture:
        raise ValueError("active corpus has no complete immutable computation-run chain")
    if row["run_id"] != computation_run_id(
        phase="embedding",
        corpus_version=str(row["version"]),
        expected_row_count=int(row["expected_row_count"]),
        provenance=provenance,
    ) or row["ingest_run_id"] != computation_run_id(
        phase="ingest",
        corpus_version=str(row["version"]),
        expected_row_count=int(row["ingest_expected_row_count"]),
        provenance=ingest_provenance,
    ):
        raise ValueError("active corpus computation run IDs are not content-addressed")
    mismatched = int(
        await connection.fetchval(
            "select count(*) from genereview.genereview_embeddings_bge384 "
            "where embedding_run_id is distinct from $1",
            row["run_id"],
        )
        or 0
    )
    if mismatched:
        raise ValueError("embedding rows are not bound to the active computation run")
    chapter_count = int(
        await connection.fetchval("select count(*) from genereview.genereview_chapters") or 0
    )
    if chapter_count != int(row["ingest_expected_row_count"]):
        raise ValueError("ingest computation run does not cover every active chapter")
    return {
        "run_id": str(row["run_id"]),
        "app_git_sha": str(row["app_git_sha"]),
        "expected_row_count": int(row["expected_row_count"]),
        "provenance": provenance,
        "ingest_run": {
            "run_id": str(row["ingest_run_id"]),
            "app_git_sha": str(row["ingest_app_git_sha"]),
            "expected_row_count": int(row["ingest_expected_row_count"]),
            "provenance": ingest_provenance,
        },
    }


__all__ = [
    "begin_embedding_run",
    "complete_embedding_run",
    "computation_run_id",
    "load_active_computation",
    "record_ingest_run",
]
