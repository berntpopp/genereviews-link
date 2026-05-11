"""Drive the embedding backfill stage with pipelined encoder + writers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from genereview_link.config import settings
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
    schema: str,
    batch_size: int,
) -> AsyncIterator[list[tuple[str, str, str]]]:
    """Yield batches of (nbk_id, passage_id, text) lacking an embedding row."""
    offset = 0
    while True:
        async with pool.acquire() as conn:
            await conn.execute(f'set search_path to "{schema}", public')
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.text
                  from genereview_passages p
                  left join genereview_embeddings_bge384 e
                    on e.nbk_id = p.nbk_id
                   and e.passage_id = p.passage_id
                   and e.model_name = $1
                 where e.passage_id is null
                 order by p.nbk_id, p.passage_id
                 limit $2 offset $3
                """,
                model_name,
                batch_size,
                offset,
            )
        if not rows:
            return
        yield [(r["nbk_id"], r["passage_id"], r["text"]) for r in rows]
        offset += batch_size


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

    encoded_q: asyncio.Queue[list[Any] | None] = asyncio.Queue(maxsize=2)
    total = 0

    async def encoder() -> None:
        async for batch in iter_passages_missing_embedding(
            pool, model_name=provider.model_name, schema=schema, batch_size=batch_size
        ):
            texts = [bge_passage_text(text) for _nbk, _pid, text in batch]
            vectors = await provider.embed_passages(texts)
            records = [
                (
                    nbk,
                    pid,
                    provider.model_name,
                    None,  # model_revision
                    text_hash(text),
                    vec,
                )
                for (nbk, pid, text), vec in zip(batch, vectors, strict=True)
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
            async with pool.acquire() as conn:
                await conn.execute(f'set search_path to "{schema}", public')
                await conn.copy_records_to_table(
                    "genereview_embeddings_bge384",
                    records=records,
                    columns=(
                        "nbk_id",
                        "passage_id",
                        "model_name",
                        "model_revision",
                        "text_hash",
                        "embedding",
                    ),
                )
            total += len(records)

    await asyncio.gather(encoder(), *(writer() for _ in range(db_writers)))
    return total


async def build_hnsw_index(pool: asyncpg.Pool, *, schema: str = "genereview") -> None:
    """Build the HNSW index post-COPY."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            create index if not exists genereview_embeddings_bge384_hnsw_cosine
                on "{schema}".genereview_embeddings_bge384
                using hnsw (embedding vector_cosine_ops)
                with (m = 16, ef_construction = 200)
            """
        )
