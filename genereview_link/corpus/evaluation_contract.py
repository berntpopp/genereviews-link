"""Stdlib-only reviewed evaluation acceptance constants."""

from __future__ import annotations

EVALUATION_SUITE_RELATIVE = "tests/eval/genereviews_queries.jsonl"
EVALUATION_SUITE_SHA256 = "22f8e5f7e7c0600d53cc8634efbb59aee1fbbe7a8e4ee9d63db360ec5df216d1"
EXPECTED_QUERY_COUNT = 5
MIN_MRR_AT_10 = 0.2619
MIN_SECTION_PRECISION_AT_5 = 0.4
EVALUATION_TOLERANCE = 1e-12

__all__ = [
    "EVALUATION_SUITE_RELATIVE",
    "EVALUATION_SUITE_SHA256",
    "EVALUATION_TOLERANCE",
    "EXPECTED_QUERY_COUNT",
    "MIN_MRR_AT_10",
    "MIN_SECTION_PRECISION_AT_5",
]
