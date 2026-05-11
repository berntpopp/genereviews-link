"""Dense score retrieval test."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.ingest.orchestrator import backfill_embeddings
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.asyncio
async def test_dense_scores_returns_cosine_in_range(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        await conn.execute(
            """
            insert into genereview_chapters (nbk_id, short_name, title, corpus_version, nxml_relpath)
            values ('NBK1', 'x', 'T', '2026', 'r')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, chunk_index, text, text_hash,
                 char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 0, 'Hello world.', 'h', 12, 3, '2026'),
                ('NBK1', 'NBK1:0002', 'summary', 1, 'Different text.', 'h', 15, 3, '2026')
            """
        )

    provider = FakeEmbeddingProvider(dim=384)
    await backfill_embeddings(pool, provider, schema="genereview")

    repo = GeneReviewRepository(pool)
    qv = await provider.embed_query("hello")
    scores = await repo.dense_scores_for_passages(
        qv,
        [("NBK1", "NBK1:0001"), ("NBK1", "NBK1:0002")],
        model_table="genereview_embeddings_bge384",
    )
    assert set(scores.keys()) == {"NBK1:0001", "NBK1:0002"}
    for v in scores.values():
        assert -1.001 <= v <= 1.001
