"""Versioned corpus evaluation replay stays compatible with published evidence."""

from __future__ import annotations

import pytest

from genereview_link.corpus import evaluation
from genereview_link.corpus.bundle_integrity import BundleIntegrityError
from genereview_link.corpus.bundle_verifier import _evaluation_algorithm


@pytest.mark.parametrize(
    ("algorithm", "expected_limit"),
    [
        (evaluation.LEGACY_EVALUATION_ALGORITHM, 10),
        (evaluation.CURRENT_EVALUATION_ALGORITHM, 200),
    ],
)
async def test_evaluation_dispatches_to_named_candidate_path(
    monkeypatch: pytest.MonkeyPatch,
    algorithm: str,
    expected_limit: int,
) -> None:
    calls: list[tuple[int, str | None]] = []

    class Repository:
        def __init__(self, _pool: object) -> None:
            pass

        async def search_passages(
            self, _query: str, *, limit: int, gene_symbol: str | None = None, **_kwargs: object
        ) -> list[object]:
            calls.append((limit, gene_symbol))
            return []

    monkeypatch.setattr(evaluation, "GeneReviewRepository", Repository)
    monkeypatch.setattr(evaluation, "assert_evaluation_accepted", lambda *_args, **_kwargs: None)

    results = await evaluation.evaluate_connection(object(), algorithm=algorithm)

    base_calls = [limit for limit, gene in calls if gene is None]
    assert base_calls == [expected_limit] * evaluation.EXPECTED_QUERY_COUNT
    if algorithm == evaluation.CURRENT_EVALUATION_ALGORITHM:
        assert [gene for _limit, gene in calls if gene is not None] == [
            "BRCA1",
            "BRCA1",
            "CFTR",
        ]
    else:
        assert len(calls) == evaluation.EXPECTED_QUERY_COUNT
    assert results["queries_run"] == evaluation.EXPECTED_QUERY_COUNT


async def test_evaluation_rejects_unknown_algorithm_before_database_access() -> None:
    with pytest.raises(
        evaluation.EvaluationRejectedError, match="unsupported evaluation algorithm"
    ):
        await evaluation.evaluate_connection(object(), algorithm="made-up-v99")


def test_new_evaluation_evidence_names_the_ranking_algorithm() -> None:
    evidence = evaluation.build_evaluation_evidence(
        {"mrr_at_10": 0.3},
        corpus_identity={},
        export_snapshot="snapshot-1",
        dump_sha256="a" * 64,
    )

    assert evidence["algorithm"] == evaluation.CURRENT_EVALUATION_ALGORITHM


def test_bundle_verifier_accepts_legacy_shape_as_legacy_replay() -> None:
    legacy = {
        "status": "passed",
        "suite": "tests/eval/genereviews_queries.jsonl",
        "suite_sha256": "a" * 64,
        "model_name": "BAAI/bge-small-en-v1.5",
        "corpus_identity": {},
        "export_snapshot": "snapshot-1",
        "dump_sha256": "b" * 64,
        "results": {},
        "result_sha256": "c" * 64,
    }

    assert _evaluation_algorithm(legacy) == evaluation.LEGACY_EVALUATION_ALGORITHM
    assert (
        _evaluation_algorithm(legacy | {"algorithm": evaluation.CURRENT_EVALUATION_ALGORITHM})
        == evaluation.CURRENT_EVALUATION_ALGORITHM
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"algorithm": "unknown-v9"},
        {"algorithm": []},
        {"unexpected": True},
    ],
)
def test_bundle_verifier_rejects_unknown_algorithm_and_schema(
    extra: dict[str, object],
) -> None:
    legacy = {
        "status": "passed",
        "suite": "tests/eval/genereviews_queries.jsonl",
        "suite_sha256": "a" * 64,
        "model_name": "BAAI/bge-small-en-v1.5",
        "corpus_identity": {},
        "export_snapshot": "snapshot-1",
        "dump_sha256": "b" * 64,
        "results": {},
        "result_sha256": "c" * 64,
    }

    with pytest.raises(BundleIntegrityError, match="evaluation"):
        _evaluation_algorithm(legacy | extra)
