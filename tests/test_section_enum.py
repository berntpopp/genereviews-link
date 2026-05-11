"""SectionName enum covers every section_priority key (and vice versa)."""

from __future__ import annotations

from genereview_link.models.sections import SECTION_NAMES, SectionName
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
