"""Build a stratified chapter worklist for benchmark seed generation.

Output: tests/fixtures/ranking_bench_worklist.json with ~30 chapters
stratified across:
  - section presence (must have >= min_sections populated sections, enforced
    via SQL HAVING clause)
  - chapter age (recent + old quarter, plus 2 'middle' quarters)

Selection: deterministic given a seed (default 42) so the worklist is
reproducible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import asyncpg

from genereview_link.db.pool import create_pool


async def fetch_candidate_chapters(min_sections: int = 3) -> list[asyncpg.Record]:
    pool = await create_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select
                    c.nbk_id,
                    c.title,
                    c.last_updated_date,
                    c.gene_symbols,
                    count(distinct p.chapter_section) filter (where p.passage_count > 0)
                        as populated_sections
                from genereview_chapters c
                join (
                    select nbk_id, chapter_section, count(*) as passage_count
                    from genereview_passages
                    group by nbk_id, chapter_section
                ) p on p.nbk_id = c.nbk_id
                group by c.nbk_id, c.title, c.last_updated_date, c.gene_symbols
                having count(distinct p.chapter_section) filter (where p.passage_count > 0) >= $1
                """,
                min_sections,
            )
        return list(rows)
    finally:
        await pool.close()


def stratify(
    rows: list[asyncpg.Record], n_per_bucket: int = 8, seed: int = 42
) -> list[dict]:
    rng = random.Random(seed)  # noqa: S311 - seeded for reproducibility, not crypto
    # Rows without last_updated_date are excluded from stratification.
    sorted_with_dates = sorted(
        [r for r in rows if r["last_updated_date"]],
        key=lambda r: r["last_updated_date"],
    )
    q = len(sorted_with_dates) // 4
    buckets = {
        "oldest_q": sorted_with_dates[:q],
        "old_mid": sorted_with_dates[q : 2 * q],
        "new_mid": sorted_with_dates[2 * q : 3 * q],
        "newest_q": sorted_with_dates[3 * q :],
    }
    selected = []
    for name, bucket in buckets.items():
        if not bucket:
            continue
        sample = rng.sample(bucket, min(n_per_bucket, len(bucket)))
        for r in sample:
            selected.append(
                {
                    "nbk_id": r["nbk_id"],
                    "title": r["title"],
                    "last_updated": (
                        str(r["last_updated_date"]) if r["last_updated_date"] else None
                    ),
                    "gene_symbols": list(r["gene_symbols"]),
                    "bucket": name,
                }
            )
    return selected


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-per-bucket", type=int, default=8)
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/ranking_bench_worklist.json"),
    )
    args = ap.parse_args()

    rows = await fetch_candidate_chapters()
    worklist = stratify(rows, n_per_bucket=args.n_per_bucket, seed=args.seed)
    args.output.write_text(json.dumps(worklist, indent=2))
    print(f"wrote {len(worklist)} chapters to {args.output}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
