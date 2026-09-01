"""Representative retrieval evaluation bound to one database transaction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from genereview_link.retrieval.repository import GeneReviewRepository

EVALUATION_SUITE = Path(__file__).resolve().parents[2] / "tests/eval/genereviews_queries.jsonl"


class _AcquireConnection:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def __aenter__(self) -> Any:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SingleConnectionPool:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def acquire(self, *, timeout: float | None = None) -> _AcquireConnection:
        del timeout
        return _AcquireConnection(self.connection)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


async def evaluate_connection(connection: Any) -> dict[str, int | float]:
    """Run the exact suite through the caller's locked repeatable-read connection."""
    repository = GeneReviewRepository(_SingleConnectionPool(connection))
    total = 0
    reciprocal_rank = 0.0
    section_hits = 0
    for line in EVALUATION_SUITE.read_text().splitlines():
        if not line.strip():
            continue
        query = json.loads(line)
        results = await repository.search_passages(query["query"], limit=10)
        total += 1
        for rank, result in enumerate(results, start=1):
            if result.passage.nbk_id == query["expected_chapter"]:
                reciprocal_rank += 1.0 / rank
                break
        if any(
            result.passage.chapter_section == query["expected_section"] for result in results[:5]
        ):
            section_hits += 1
    return {
        "mrr_at_10": reciprocal_rank / max(total, 1),
        "section_precision_at_5": section_hits / max(total, 1),
        "queries_run": total,
    }


def build_evaluation_evidence(
    metrics: dict[str, int | float],
    *,
    corpus_identity: dict[str, object],
    export_snapshot: str,
    dump_sha256: str,
) -> dict[str, object]:
    return {
        "status": "passed",
        "suite": "tests/eval/genereviews_queries.jsonl",
        "suite_sha256": hashlib.sha256(EVALUATION_SUITE.read_bytes()).hexdigest(),
        "model_name": "BAAI/bge-small-en-v1.5",
        "corpus_identity": corpus_identity,
        "export_snapshot": export_snapshot,
        "dump_sha256": dump_sha256,
        "results": metrics,
        "result_sha256": hashlib.sha256(canonical_json(metrics)).hexdigest(),
    }


__all__ = ["build_evaluation_evidence", "canonical_json", "evaluate_connection"]
