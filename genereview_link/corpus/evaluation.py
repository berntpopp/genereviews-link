"""Representative retrieval evaluation bound to one database transaction."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from genereview_link.corpus.evaluation_contract import (
    EVALUATION_SUITE_RELATIVE,
    EVALUATION_SUITE_SHA256,
    EVALUATION_TOLERANCE,
    EXPECTED_QUERY_COUNT,
    MIN_MRR_AT_10,
    MIN_SECTION_PRECISION_AT_5,
)
from genereview_link.retrieval.repository import GeneReviewRepository

EVALUATION_SUITE = Path(__file__).with_name("data") / "genereviews_queries.jsonl"


class EvaluationRejectedError(ValueError):
    """The reviewed retrieval suite did not meet its acceptance contract."""


def assert_evaluation_accepted(
    metrics: dict[str, object], *, expected_queries: int, covered_queries: int
) -> None:
    mrr = metrics.get("mrr_at_10")
    precision = metrics.get("section_precision_at_5")
    if (
        metrics.get("queries_run") != expected_queries
        or covered_queries != expected_queries
        or type(mrr) not in {int, float}
        or type(precision) not in {int, float}
        or not math.isfinite(cast(float, mrr))
        or not math.isfinite(cast(float, precision))
        or cast(float, mrr) + EVALUATION_TOLERANCE < MIN_MRR_AT_10
        or cast(float, precision) + EVALUATION_TOLERANCE < MIN_SECTION_PRECISION_AT_5
    ):
        raise EvaluationRejectedError("evaluation does not meet reviewed floors and full coverage")


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


async def evaluate_connection(connection: Any) -> dict[str, object]:
    """Run the exact suite through the caller's locked repeatable-read connection."""
    suite_bytes = EVALUATION_SUITE.read_bytes()
    if hashlib.sha256(suite_bytes).hexdigest() != EVALUATION_SUITE_SHA256:
        raise EvaluationRejectedError("reviewed evaluation suite bytes do not match their SHA-256")
    repository = GeneReviewRepository(_SingleConnectionPool(connection))
    total = 0
    reciprocal_rank = 0.0
    section_hits = 0
    covered = 0
    per_query: list[dict[str, object]] = []
    for line in suite_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        query = json.loads(line)
        results = await repository.search_passages(query["query"], limit=10)
        total += 1
        if results:
            covered += 1
        expected_rank: int | None = None
        for rank, result in enumerate(results, start=1):
            if result.passage.nbk_id == query["expected_chapter"]:
                reciprocal_rank += 1.0 / rank
                expected_rank = rank
                break
        section_hit = any(
            result.passage.chapter_section == query["expected_section"] for result in results[:5]
        )
        if section_hit:
            section_hits += 1
        per_query.append(
            {
                "query_sha256": hashlib.sha256(query["query"].encode()).hexdigest(),
                "expected_chapter": query["expected_chapter"],
                "expected_section": query["expected_section"],
                "expected_rank": expected_rank,
                "section_hit_at_5": section_hit,
                "results_returned": len(results),
            }
        )
    metrics: dict[str, object] = {
        "mrr_at_10": reciprocal_rank / max(total, 1),
        "section_precision_at_5": section_hits / max(total, 1),
        "queries_run": total,
        "covered_queries": covered,
        "per_query": per_query,
    }
    assert_evaluation_accepted(
        metrics,
        expected_queries=EXPECTED_QUERY_COUNT,
        covered_queries=covered,
    )
    return metrics


def build_evaluation_evidence(
    metrics: dict[str, object],
    *,
    corpus_identity: dict[str, object],
    export_snapshot: str,
    dump_sha256: str,
) -> dict[str, object]:
    return {
        "status": "passed",
        "suite": EVALUATION_SUITE_RELATIVE,
        "suite_sha256": EVALUATION_SUITE_SHA256,
        "model_name": "BAAI/bge-small-en-v1.5",
        "corpus_identity": corpus_identity,
        "export_snapshot": export_snapshot,
        "dump_sha256": dump_sha256,
        "results": metrics,
        "result_sha256": hashlib.sha256(canonical_json(metrics)).hexdigest(),
    }


__all__ = [
    "EvaluationRejectedError",
    "assert_evaluation_accepted",
    "build_evaluation_evidence",
    "canonical_json",
    "evaluate_connection",
]
