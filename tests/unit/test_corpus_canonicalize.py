"""Tests for section-name canonicalization."""

from __future__ import annotations

import pytest

from genereview_link.corpus.canonicalize import (
    CANONICAL_SECTIONS,
    canonical_section,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Summary", "summary"),
        ("SUMMARY", "summary"),
        ("Diagnosis", "diagnosis"),
        ("Diagnosis/Testing", "diagnosis"),
        ("Establishing the Diagnosis", "diagnosis"),
        ("Clinical Description", "clinical_features"),
        ("Clinical Characteristics", "clinical_features"),
        ("Differential Diagnosis", "clinical_features"),
        ("Management", "management"),
        ("Treatment of Manifestations", "management"),
        ("Surveillance", "management"),
        ("Genetic Counseling", "genetic_counseling"),
        ("Molecular Genetics", "molecular_genetics"),
        ("Pathogenic variants", "molecular_genetics"),
        ("Resources", "resources"),
        ("References", "references"),
        ("Some Other Heading", "other"),
        ("", "other"),
    ],
)
def test_canonical_section_maps_titles(title: str, expected: str) -> None:
    assert canonical_section(title) == expected


def test_canonical_sections_are_documented() -> None:
    assert {
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "references",
        "other",
    } <= CANONICAL_SECTIONS
