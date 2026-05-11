"""Compute MRR@10 and section-precision@5 for the eval set."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from genereview_link.db.pool import create_pool
from genereview_link.retrieval.repository import GeneReviewRepository

QUERIES = Path(__file__).parent / "genereviews_queries.jsonl"
BASELINE = Path(__file__).parent / "baseline.json"


async def run() -> dict[str, float]:
    pool = await create_pool()
    repo = GeneReviewRepository(pool)
    try:
        total = 0
        mrr_sum = 0.0
        section_hits = 0
        for line in QUERIES.read_text().splitlines():
            if not line.strip():
                continue
            q = json.loads(line)
            results = await repo.search_passages(q["query"], limit=10)
            total += 1
            for i, r in enumerate(results, start=1):
                if r.passage.nbk_id == q["expected_chapter"]:
                    mrr_sum += 1.0 / i
                    break
            top5 = results[:5]
            if any(r.passage.chapter_section == q["expected_section"] for r in top5):
                section_hits += 1
        return {
            "mrr_at_10": mrr_sum / max(total, 1),
            "section_precision_at_5": section_hits / max(total, 1),
            "queries_run": total,
        }
    finally:
        await pool.close()


if __name__ == "__main__":
    metrics = asyncio.run(run())
    print(json.dumps(metrics, indent=2))
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text())
        for k in ("mrr_at_10", "section_precision_at_5"):
            delta = metrics[k] - baseline.get(k, 0.0)
            if delta < -0.05:
                print(f"REGRESSION: {k} dropped by {-delta:.3f}")
                sys.exit(1)
