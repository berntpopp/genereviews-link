"""Measure HNSW + filter query plans via EXPLAIN ANALYZE for C-alpha.

Runs EXPLAIN (ANALYZE, FORMAT JSON, BUFFERS) against each filter combination
supported by build_dense_candidates_sql and records the planner node type,
total cost, actual execution time, and rows returned.

Output:
  c_alpha_hnsw_plans.json  -- list of plan detail records
  console table            -- case, node_type, time_ms, rows
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

from genereview_link.db.pool import create_pool
from genereview_link.retrieval.repository import build_dense_candidates_sql

EMBEDDING_TABLE = "genereview_embeddings_bge384"
TOP_K = 200
DUMMY_VECTOR: list[float] = [0.0] * 384
JSON_OUT = Path("c_alpha_hnsw_plans.json")

# ---------------------------------------------------------------------------
# Filter cases (6 required by the plan)
# ---------------------------------------------------------------------------

CASES: list[dict[str, Any]] = [
    {
        "name": "no_filter",
        "gene": None,
        "nbk_id": None,
        "sections": None,
        "heading_path_contains": None,
    },
    {
        "name": "gene_only",
        "gene": "HFE",
        "nbk_id": None,
        "sections": None,
        "heading_path_contains": None,
    },
    {
        "name": "nbk_id_only",
        "gene": None,
        "nbk_id": "NBK1247",
        "sections": None,
        "heading_path_contains": None,
    },
    {
        "name": "section_only",
        "gene": None,
        "nbk_id": None,
        "sections": ("management",),
        "heading_path_contains": None,
    },
    {
        "name": "gene_plus_section",
        "gene": "BRCA1",
        "nbk_id": None,
        "sections": ("management",),
        "heading_path_contains": None,
    },
    {
        "name": "heading_path_only",
        "gene": None,
        "nbk_id": None,
        "sections": None,
        "heading_path_contains": "Prevention",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _top_node(plan_json: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the top-level Plan node from a single-statement EXPLAIN JSON."""
    # fetchval returns the JSON array as a Python list (asyncpg decodes it).
    return plan_json[0]["Plan"]  # type: ignore[index]


def _extract_metrics(
    node: dict[str, Any],
) -> tuple[str, float, float, int]:
    """Return (node_type, total_cost, actual_time_ms, actual_rows)."""
    node_type = str(node.get("Node Type", "unknown"))
    total_cost = float(node.get("Total Cost", 0.0))
    actual_time = float(node.get("Actual Total Time", 0.0))
    actual_rows = int(node.get("Actual Rows", 0))
    return node_type, total_cost, actual_time, actual_rows


# ---------------------------------------------------------------------------
# Main measurement
# ---------------------------------------------------------------------------


async def measure_case(
    conn: asyncpg.Connection,
    case: dict[str, Any],
) -> dict[str, Any]:
    """Run EXPLAIN ANALYZE for one filter case and return metrics."""
    case_params = {k: v for k, v in case.items() if k != "name"}
    setup, select_sql, params = build_dense_candidates_sql(
        embedding_table=EMBEDDING_TABLE,
        top_k=TOP_K,
        **case_params,
    )
    # Fill in the dummy query vector (first element is the placeholder).
    params[0] = DUMMY_VECTOR

    explain_sql = "EXPLAIN (ANALYZE, FORMAT JSON, BUFFERS) " + select_sql

    async with conn.transaction():
        for stmt in setup:
            await conn.execute(stmt)
        raw = await conn.fetchval(explain_sql, *params)

    # asyncpg returns JSON columns as strings when the column has no explicit type
    # annotation.  Handle both str and already-decoded list.
    if isinstance(raw, str):
        plan_json: list[dict[str, Any]] = json.loads(raw)
    else:
        plan_json = raw  # type: ignore[assignment]

    node = _top_node(plan_json)
    node_type, total_cost, actual_time, actual_rows = _extract_metrics(node)

    return {
        "case": case["name"],
        "node_type": node_type,
        "total_cost": total_cost,
        "actual_time_ms": round(actual_time, 3),
        "rows_returned": actual_rows,
        "plan_json": plan_json,
    }


async def main() -> None:
    pool: asyncpg.Pool = await create_pool()
    results: list[dict[str, Any]] = []

    print(f"\n{'case':<24} {'node_type':<40} {'time_ms':>10} {'rows':>8}")  # noqa: T201
    print("-" * 86)  # noqa: T201

    try:
        async with pool.acquire() as conn:
            for case in CASES:
                try:
                    rec = await measure_case(conn, case)
                except Exception as exc:
                    print(f"  {case['name']:<23} ERROR: {exc}", file=sys.stderr)  # noqa: T201
                    continue
                results.append(rec)
                print(  # noqa: T201
                    f"  {rec['case']:<23} {rec['node_type']:<40}"
                    f" {rec['actual_time_ms']:>10.1f} {rec['rows_returned']:>8}"
                )
    finally:
        await pool.close()

    print()  # noqa: T201

    # Write JSON (strip plan_json for brevity in the summary, keep full object)
    JSON_OUT.write_text(
        json.dumps(
            results,
            indent=2,
            default=str,
        )
    )
    print(f"Wrote {len(results)} records to {JSON_OUT}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
