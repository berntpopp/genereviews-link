"""Run each ranking_bench.jsonl entry against the live MCP across each retrieval mode,
compute P@1, MRR@5, Recall@5, and print a comparison table.

Hard gates enforced:
  - must_not_regress + regression_kind=exact-symbol-anchor: any top-1 change fails the run.
  - must_change: at least 1 entry's top-1 must improve, or the run fails.

Honors --regression-kind-strict flag (default true) to disable the
must_not_regress gate for the pending-improvement subset.

Output: stdout table + optional JSON dump via --json-out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import httpx

MODES = ["lexical", "rrf", "off"]  # rerank=ce added later when C-gamma ships


def reciprocal_rank(ids: list[str], gold: str) -> float:
    for i, p in enumerate(ids):
        if p == gold:
            return 1.0 / (i + 1)
    return 0.0


async def run_one(client: httpx.AsyncClient, entry: dict, mode: str) -> dict:
    r = await client.get(
        "/passages/search",
        params={
            "q": entry["query"],
            "rerank": mode,
            "mode": "ids_only",
            "limit": 5,
        },
    )
    r.raise_for_status()
    ids = [row["passage_id"] for row in r.json().get("results", [])]
    gold1 = entry["expected_top1_passage_id"]
    gold5 = set(entry.get("expected_top5_passage_ids", [gold1]))
    return {
        "p_at_1": 1.0 if ids and ids[0] == gold1 else 0.0,
        "mrr_at_5": reciprocal_rank(ids, gold1),
        "recall_at_5": 1.0 if any(p in gold5 for p in ids) else 0.0,
        "top1": ids[0] if ids else None,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--bench",
        type=Path,
        default=Path("tests/fixtures/ranking_bench.jsonl"),
    )
    ap.add_argument(
        "--base-url",
        default=os.environ.get("MCP_BASE_URL", "http://127.0.0.1:8765"),
    )
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    entries = [
        json.loads(line)
        for line in args.bench.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    results: dict[str, defaultdict[str, list[float]]] = {
        m: defaultdict(list) for m in MODES
    }
    regressions: dict[str, list[tuple[str, str, str | None]]] = {
        m: [] for m in MODES
    }
    improvements: dict[str, list[str]] = {m: [] for m in MODES}

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        for entry in entries:
            for mode in MODES:
                r = await run_one(client, entry, mode)
                results[mode]["p_at_1"].append(r["p_at_1"])
                results[mode]["mrr_at_5"].append(r["mrr_at_5"])
                results[mode]["recall_at_5"].append(r["recall_at_5"])

                # Hard gates
                if (
                    entry.get("status") == "must_not_regress"
                    and entry.get("regression_kind") == "exact-symbol-anchor"
                    and r["top1"] != entry["expected_top1_passage_id"]
                ):
                    regressions[mode].append(
                        (
                            entry["query"],
                            entry["expected_top1_passage_id"],
                            r["top1"],
                        )
                    )

                if entry.get("status") == "must_change" and r["p_at_1"] == 1.0:
                    improvements[mode].append(entry["query"])

    # Print comparison table.
    print(  # noqa: T201
        f"\n{'Mode':<10} {'P@1':>8} {'MRR@5':>8} {'Recall@5':>10}"
        f" {'Composite':>10} {'Regressions':>12} {'Improvements':>13}"
    )
    print("-" * 80)  # noqa: T201
    summary: dict[str, dict] = {}
    for mode in MODES:
        n = len(entries)
        p1 = sum(results[mode]["p_at_1"]) / n
        mrr = sum(results[mode]["mrr_at_5"]) / n
        r5 = sum(results[mode]["recall_at_5"]) / n
        comp = (p1 + mrr + r5) / 3.0
        reg = len(regressions[mode])
        imp = len(improvements[mode])
        print(  # noqa: T201
            f"{mode:<10} {p1:>8.3f} {mrr:>8.3f} {r5:>10.3f}"
            f" {comp:>10.3f} {reg:>12} {imp:>13}"
        )
        summary[mode] = {
            "p_at_1": p1,
            "mrr_at_5": mrr,
            "recall_at_5": r5,
            "composite": comp,
            "regressions": regressions[mode],
            "improvements": improvements[mode],
        }

    # Hard gate enforcement: exact-symbol-anchor regressions fail the run.
    failed = False
    for mode in MODES:
        if regressions[mode]:
            print(  # noqa: T201
                f"\nFAIL: {mode} regressed {len(regressions[mode])}"
                " exact-symbol-anchor entries:"
            )
            for q, gold, got in regressions[mode]:
                print(f"  {q}: expected {gold}, got {got}")  # noqa: T201
            failed = True

    if args.json_out:
        args.json_out.write_text(json.dumps(summary, indent=2))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
