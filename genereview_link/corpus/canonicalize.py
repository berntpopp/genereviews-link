"""Map free-form GeneReviews section titles to the closed canonical vocabulary.

The closed vocabulary feeds retrieval/rerank.py SECTION_PRIORITY and lets
operators reliably filter by section.
"""

from __future__ import annotations

import re

CANONICAL_SECTIONS: frozenset[str] = frozenset(
    {
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "references",
        "other",
    }
)


# Ordered: first match wins. Patterns are case-insensitive whole-token matches.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*summary\b", re.I), "summary"),
    (re.compile(r"^\s*references?\b", re.I), "references"),
    (re.compile(r"^\s*resources\b", re.I), "resources"),
    (re.compile(r"genetic\s+counsel", re.I), "genetic_counseling"),
    (re.compile(r"molecular\s+genetics", re.I), "molecular_genetics"),
    (re.compile(r"pathogenic\s+variants?", re.I), "molecular_genetics"),
    (re.compile(r"differential\s+diagnos", re.I), "clinical_features"),
    (re.compile(r"clinical\s+(description|characteristics|features)", re.I), "clinical_features"),
    (re.compile(r"diagnos", re.I), "diagnosis"),
    (re.compile(r"^\s*(treatment|surveillance|management|therapy|prevention)\b", re.I), "management"),
)


def canonical_section(title: str | None) -> str:
    """Return the canonical section key for a free-form chapter section title."""
    if not title:
        return "other"
    for pattern, canonical in _RULES:
        if pattern.search(title):
            return canonical
    return "other"
