"""Runtime-safe PostgreSQL vector index operations."""

from __future__ import annotations

import asyncpg

from genereview_link.db.identifiers import quote_pg_identifier
from genereview_link.db.locks import CORPUS_WRITE_LOCK_KEY


async def build_hnsw_index(pool: asyncpg.Pool, *, schema: str = "genereview") -> None:
    """Build the reviewed HNSW index after a data-only restore or embedding COPY."""
    quoted_schema = quote_pg_identifier(schema)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        await connection.execute(
            f"""
                create index if not exists genereview_embeddings_bge384_hnsw_cosine
                    on {quoted_schema}.genereview_embeddings_bge384
                    using hnsw (embedding vector_cosine_ops)
                    with (m = 16, ef_construction = 200)
                """
        )


async def rebuild_hnsw_index(pool: asyncpg.Pool, *, schema: str = "genereview") -> None:
    """Rebuild only the reviewed HNSW index without inserting embeddings."""
    quoted_schema = quote_pg_identifier(schema)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        await connection.execute("drop index if exists genereview_embeddings_bge384_hnsw_cosine")
        await connection.execute(
            f"""
                create index genereview_embeddings_bge384_hnsw_cosine
                    on {quoted_schema}.genereview_embeddings_bge384
                    using hnsw (embedding vector_cosine_ops)
                    with (m = 16, ef_construction = 200)
                """
        )


__all__ = ["build_hnsw_index", "rebuild_hnsw_index"]
