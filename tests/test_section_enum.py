"""SectionName enum covers every section_priority key (and vice versa)."""

from __future__ import annotations

from genereview_link.models.sections import SECTION_NAMES, SectionName, canonicalize_nbk_id
from genereview_link.retrieval.rerank import SECTION_PRIORITY


def test_section_names_is_tuple_of_strings() -> None:
    assert isinstance(SECTION_NAMES, tuple)
    assert all(isinstance(n, str) for n in SECTION_NAMES)


def test_section_names_covers_section_priority_keys() -> None:
    assert set(SECTION_PRIORITY.keys()) == set(SECTION_NAMES), (
        "SECTION_NAMES and SECTION_PRIORITY drifted; update both."
    )


def test_section_name_literal_includes_expected_canonical_names() -> None:
    expected = {
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "other",
        "references",
    }
    assert expected.issubset(set(SECTION_NAMES))


def test_section_name_is_literal_type() -> None:
    # SectionName must be usable as a Pydantic field type and emit a
    # JSONSchema enum. We check the runtime args match SECTION_NAMES.
    from typing import get_args

    assert tuple(get_args(SectionName)) == SECTION_NAMES


def test_canonicalize_nbk_id_strips_leading_zeroes() -> None:
    assert canonicalize_nbk_id("NBK0001247") == "NBK1247"
    assert canonicalize_nbk_id("NBK1247") == "NBK1247"
    assert canonicalize_nbk_id("NBK0") == "NBK0"
    assert canonicalize_nbk_id("NBK000") == "NBK0"
    assert canonicalize_nbk_id("ABC") == "ABC"


def test_canonicalize_nbk_id_handles_pathological_input_fast() -> None:
    # Regression for CodeQL py/polynomial-redos: an all-zero digit run that
    # never reaches a terminating non-zero digit must not cause catastrophic
    # backtracking. The rewritten linear pattern returns effectively instantly.
    import time

    pathological = "NBK" + "0" * 50_000
    start = time.perf_counter()
    result = canonicalize_nbk_id(pathological)
    elapsed = time.perf_counter() - start

    assert result == "NBK0"
    assert elapsed < 1.0, f"canonicalize_nbk_id too slow ({elapsed:.3f}s); ReDoS regression"
