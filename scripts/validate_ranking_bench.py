"""Run each silver entry through the live MCP across non-CE retrieval modes
(lexical, rrf-CURRENT-PRE-UPGRADE, off), assign bucket A/B/C, write back to
ranking_bench.jsonl with a 'validation_bucket' field.

Bucket A: all 3 modes return gold in top-5  -> silver confirmed.
Bucket B: modes disagree on top-5           -> flag for SME spot-check.
Bucket C: no mode returns gold in top-50    -> either hard-recall test or bogus.

Note: at this point the parallel-retrieval rrf is NOT yet implemented.
This task runs against the CURRENT (gated) rrf, which gives us the baseline
to measure against after C-alpha's main code change lands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

import httpx


async def query_mode(
    client: httpx.AsyncClient,
    q: str,
    mode: str,
    gold_id: str,
) -> tuple[bool, bool, str | None]:
    """Return (in_top5, in_top50, top1_id) for a single retrieval mode."""
    r = await client.get(
        "/passages/search",
        params={"q": q, "rerank": mode, "mode": "ids_only", "limit": 50},
    )
    r.raise_for_status()
    rows = r.json().get("results", [])
    ids = [row["passage_id"] for row in rows]
    return (
        gold_id in ids[:5],
        gold_id in ids[:50],
        ids[0] if ids else None,
    )


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate silver ranking bench entries against live MCP retrieval modes."
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=Path("tests/fixtures/ranking_bench.jsonl"),
    )
    ap.add_argument(
        "--base-url",
        default=os.environ.get("MCP_BASE_URL", "http://127.0.0.1:8765"),
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-validate even entries that already have a validation_bucket",
    )
    args = ap.parse_args()

    entries = [
        json.loads(line)
        for line in args.input.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        for e in entries:
            if e.get("validation_bucket") and not args.force:
                # Already validated; preserve unless caller explicitly clears.
                print(  # noqa: T201
                    f"  {e['expected_top1_passage_id']}: bucket={e['validation_bucket']} (cached)",
                    flush=True,
                )
                continue

            gold = e["expected_top1_passage_id"]
            modes = ["lexical", "rrf", "off"]
            results: dict[str, tuple[bool, bool, str | None] | tuple[bool, bool, str]] = {}
            for m in modes:
                try:
                    results[m] = await query_mode(client, e["query"], m, gold)
                except Exception as ex:
                    results[m] = (False, False, f"error: {ex}")

            top5 = [r[0] for r in results.values() if isinstance(r[0], bool)]
            top50 = [r[1] for r in results.values() if isinstance(r[1], bool)]

            if all(top5):
                e["validation_bucket"] = "A"
            elif any(top50):
                e["validation_bucket"] = "B"
            else:
                e["validation_bucket"] = "C"

            e["validation_detail"] = {m: {"top1": r[2]} for m, r in results.items()}

            print(  # noqa: T201
                f"  {e.get('expected_top1_passage_id', '?')}: bucket={e['validation_bucket']}",
                flush=True,
            )

    # Rewrite the file with bucket annotations.
    with args.input.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    print("buckets:", Counter(e["validation_bucket"] for e in entries))  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
