"""Helpers for the three-tsquery lexical search.

Ported from pubtator-link with renames.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "in", "is", "it",
        "to", "for", "on", "at", "be", "as", "by", "do", "up",
        "if", "no", "so", "we", "he", "she", "they", "you", "our",
        "are", "was", "not", "but", "has", "had", "its", "can",
        "may", "who", "how", "all", "one", "two",
    }
)


def recall_terms(query: str) -> list[str]:
    """Extract distinct 3+-char lowercased tokens from *query*, excluding stop words."""
    tokens = (m.group(0).lower() for m in _TOKEN_RE.finditer(query))
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if len(tok) < 3:
            continue
        if tok in _STOP_WORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def recall_tsquery(query: str) -> str:
    """Build an OR-joined to_tsquery from *query* tokens."""
    terms = recall_terms(query)
    if not terms:
        return "x:*"  # safe, matches nothing meaningful but parses
    return " | ".join(terms)
