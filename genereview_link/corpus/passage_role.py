from __future__ import annotations

import re
from typing import Final, Literal

PassageRole = Literal["evidence", "cross_reference", "definition", "table_caption", "table_body"]

_TABLE_CAPTION_TEXT_MULTIPLIER: Final = 2.0
_TABLE_CAPTION_TEXT_BUFFER: Final = 50

_CROSS_REFERENCE_MAX_CHARS: Final = 200
_CROSS_REFERENCE_HEADING_SUFFIXES: Final = (
    "Related",
    "Issues",
    "Counseling",
    "Information",
    "Evaluation of Relatives at Risk",
    "GeneReview Scope",
)
_CROSS_REFERENCE_PHRASES: Final = (
    "refer to ",
    "for details",
    "for more information",
    "described in",
)
_SEE_TRIGGER_RE: Final = re.compile(r"\bsee\s+", re.IGNORECASE)

_DEFINITION_MAX_CHARS: Final = 120
_TERM_COLON_RE: Final = re.compile(r"^\w[\w\s\-/()]{0,40}:\s+\S")
_NOMENCLATURE_DEFINITION_RES: Final = (
    re.compile(r"^\w[\w\s\-/()]{0,60} is an? synonym of \S", re.IGNORECASE),
    re.compile(r"^\w[\w\s\-/()]{0,60} is an? historical name for \S", re.IGNORECASE),
    re.compile(r"^\w[\w\s\-/()]{0,60} has also been referred to as \S", re.IGNORECASE),
    re.compile(r"^\w[\w\s\-/()]{0,60} was initially named \S", re.IGNORECASE),
    re.compile(r"^\w[\w\s\-/()]{0,60} was previously referred to as \S", re.IGNORECASE),
)


def classify_passage_role(
    *,
    text: str,
    heading_path: str,
    passage_type: str,
    char_count: int,
    caption_text: str = "",
) -> PassageRole:
    if passage_type == "table":
        if caption_text and char_count <= _table_caption_char_limit(caption_text):
            return "table_caption"
        return "table_body"

    if _is_cross_reference(text=text, heading_path=heading_path, char_count=char_count):
        return "cross_reference"

    if _is_definition(text=text, heading_path=heading_path, char_count=char_count):
        return "definition"

    return "evidence"


def _table_caption_char_limit(caption_text: str) -> float:
    return len(caption_text) * _TABLE_CAPTION_TEXT_MULTIPLIER + _TABLE_CAPTION_TEXT_BUFFER


def _is_cross_reference(*, text: str, heading_path: str, char_count: int) -> bool:
    if char_count > _CROSS_REFERENCE_MAX_CHARS or not _has_cross_reference_trigger(text):
        return False
    return _has_cross_reference_heading(heading_path)


def _has_cross_reference_trigger(text: str) -> bool:
    lower_text = text.lower()
    return bool(_SEE_TRIGGER_RE.search(text)) or any(
        phrase in lower_text for phrase in _CROSS_REFERENCE_PHRASES
    )


def _has_cross_reference_heading(heading_path: str) -> bool:
    headings = [heading.strip() for heading in heading_path.split(" > ") if heading.strip()]
    if not headings:
        return False
    return headings[-1].endswith(_CROSS_REFERENCE_HEADING_SUFFIXES)


def _is_definition(*, text: str, heading_path: str, char_count: int) -> bool:
    if char_count > _DEFINITION_MAX_CHARS:
        return False

    stripped_text = text.strip()
    if _TERM_COLON_RE.match(stripped_text):
        return True

    headings = [heading.strip() for heading in heading_path.split(" > ") if heading.strip()]
    if not headings or headings[-1] != "Nomenclature":
        return False
    return any(pattern.match(stripped_text) for pattern in _NOMENCLATURE_DEFINITION_RES)
