"""Drive the embedding backfill stage with pipelined encoder + writers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from genereview_link.config import settings
from genereview_link.db.identifiers import quote_pg_identifier
from genereview_link.db.indexes import build_hnsw_index as build_hnsw_index
from genereview_link.db.indexes import rebuild_hnsw_index as rebuild_hnsw_index
from genereview_link.db.locks import CORPUS_WRITE_LOCK_KEY
from genereview_link.retrieval.embeddings import (
    EmbeddingProvider,
    bge_passage_text,
    text_hash,
)

logger = logging.getLogger(__name__)


async def iter_passages_missing_embedding(
    pool: asyncpg.Pool,
    *,
    model_name: str,
    model_revision: str,
    embedding_run_id: str | None = None,
    schema: str,
    batch_size: int,
) -> AsyncIterator[list[tuple[str, str, str, str]]]:
    """Yield batches of (nbk_id, passage_id, text, passage_type) lacking an embedding row.

    Uses a keyset (>(nbk_id, passage_id)) cursor instead of OFFSET so newly
    inserted embedding rows do not shift the result window — every passage
    is visited exactly once.
    """
    quoted_schema = quote_pg_identifier(schema)
    last_nbk = ""
    last_pid = ""
    while True:
        async with pool.acquire() as conn:
            await conn.execute(f"set search_path to {quoted_schema}, public")
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.text, p.passage_type
                  from genereview_passages p
                  left join genereview_embeddings_bge384 e
                    on e.nbk_id = p.nbk_id
                   and e.passage_id = p.passage_id
                   and e.model_name = $1
                 where (e.passage_id is null
                        or e.model_revision is distinct from $2
                        or ($3::text is not null and e.embedding_run_id is distinct from $3))
                   and (p.nbk_id, p.passage_id) > ($4, $5)
                 order by p.nbk_id, p.passage_id
                 limit $6
                """,
                model_name,
                model_revision,
                embedding_run_id,
                last_nbk,
                last_pid,
                batch_size,
            )
        if not rows:
            return
        yield [(r["nbk_id"], r["passage_id"], r["text"], r["passage_type"]) for r in rows]
        last_nbk = rows[-1]["nbk_id"]
        last_pid = rows[-1]["passage_id"]


async def backfill_embeddings(
    pool: asyncpg.Pool,
    provider: EmbeddingProvider,
    *,
    schema: str = "genereview",
    batch_size: int | None = None,
    db_writers: int | None = None,
) -> int:
    """Encode and COPY embeddings for all unembedded passages in *schema*."""
    batch_size = batch_size or settings.INGEST_EMBED_BATCH_SIZE
    db_writers = db_writers or settings.INGEST_EMBED_WRITERS

    quoted_schema = quote_pg_identifier(schema)
    run_id: str | None = None
    if schema == "genereview":
        from genereview_link.corpus.computation_runs import begin_embedding_run

        async with pool.acquire() as connection:
            expected_count = int(
                await connection.fetchval("select count(*) from genereview.genereview_passages")
                or 0
            )
        run = await begin_embedding_run(pool, expected_row_count=expected_count)
        if run is not None:
            run_id = run[0]
    encoded_q: asyncio.Queue[list[Any] | None] = asyncio.Queue(maxsize=2)
    total = 0

    async def encoder() -> None:
        async for batch in iter_passages_missing_embedding(
            pool,
            model_name=provider.model_name,
            model_revision=provider.model_revision,
            embedding_run_id=run_id,
            schema=schema,
            batch_size=batch_size,
        ):
            texts = [
                bge_passage_text(text, passage_type=ptype) for _nbk, _pid, text, ptype in batch
            ]
            vectors = await provider.embed_passages(texts)
            records = [
                (
                    nbk,
                    pid,
                    provider.model_name,
                    provider.model_revision,
                    text_hash(text),
                    vec,
                    run_id,
                )
                for (nbk, pid, text, _ptype), vec in zip(batch, vectors, strict=True)
            ]
            await encoded_q.put(records)
        for _ in range(db_writers):
            await encoded_q.put(None)

    async def writer() -> None:
        nonlocal total
        while True:
            records = await encoded_q.get()
            if records is None:
                return
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
                await conn.execute(f"set search_path to {quoted_schema}, public")
                await conn.executemany(
                    """
                    insert into genereview_embeddings_bge384
                        (nbk_id, passage_id, model_name, model_revision, text_hash,
                         embedding, embedding_run_id)
                    values ($1, $2, $3, $4, $5, $6, $7)
                    on conflict (nbk_id, passage_id) do update
                       set model_name = excluded.model_name,
                           model_revision = excluded.model_revision,
                           text_hash = excluded.text_hash,
                           embedding = excluded.embedding,
                           embedding_run_id = excluded.embedding_run_id,
                           created_at = now()
                     where genereview_embeddings_bge384.model_revision is distinct from
                           excluded.model_revision
                        or genereview_embeddings_bge384.model_name is distinct from
                           excluded.model_name
                        or genereview_embeddings_bge384.text_hash is distinct from
                           excluded.text_hash
                        or genereview_embeddings_bge384.embedding_run_id is distinct from
                           excluded.embedding_run_id
                    """,
                    records,
                )
            total += len(records)

    await asyncio.gather(encoder(), *(writer() for _ in range(db_writers)))
    if run_id is not None:
        from genereview_link.corpus.computation_runs import complete_embedding_run

        await complete_embedding_run(pool, run_id=run_id)
    return total
