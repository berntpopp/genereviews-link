from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict, cast, get_args

import pytest

from genereview_link.corpus.passage_role import PassageRole, classify_passage_role

ExpectedRole = Literal["evidence", "cross_reference", "definition", "table_caption", "table_body"]


class LabeledPassage(TypedDict):
    passage_id: str
    text: str
    heading_path: str
    passage_type: str
    char_count: int
    caption_text: str
    expected_role: ExpectedRole
    notes: str


def _load_labeled_passages() -> list[LabeledPassage]:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "labeled_passages.jsonl"
    rows: list[LabeledPassage] = []
    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(cast(LabeledPassage, json.loads(line)))
    return rows


def test_passage_role_literal_values_are_exact() -> None:
    assert get_args(PassageRole) == (
        "evidence",
        "cross_reference",
        "definition",
        "table_caption",
        "table_body",
    )


@pytest.mark.parametrize("text", ["Seemingly related information.", "Seek related information."])
def test_cross_reference_see_trigger_is_word_bounded(text: str) -> None:
    assert (
        classify_passage_role(
            text=text,
            heading_path="Resources > Related Information",
            passage_type="narrative",
            char_count=len(text),
        )
        == "evidence"
    )


def test_cross_reference_requires_allowed_heading_suffix() -> None:
    assert (
        classify_passage_role(
            text="See Genetic Counseling for issues related to testing.",
            heading_path="Summary",
            passage_type="narrative",
            char_count=51,
        )
        == "evidence"
    )


def test_table_role_uses_caption_length_threshold() -> None:
    caption_text = "Short caption"

    assert (
        classify_passage_role(
            text=f"{caption_text}\n\n| A |",
            heading_path="Management > Table 1",
            passage_type="table",
            char_count=20,
            caption_text=caption_text,
        )
        == "table_caption"
    )
    assert (
        classify_passage_role(
            text=f"{caption_text}\n\n| {'x' * 200} |",
            heading_path="Management > Table 1",
            passage_type="table",
            char_count=200,
            caption_text=caption_text,
        )
        == "table_body"
    )


def test_fixture_role_classification_reaches_required_accuracy() -> None:
    rows = _load_labeled_passages()
    mismatches: list[dict[str, Any]] = []

    for row in rows:
        predicted = classify_passage_role(
            text=row["text"],
            heading_path=row["heading_path"],
            passage_type=row["passage_type"],
            char_count=row["char_count"],
            caption_text=row["caption_text"],
        )
        if predicted != row["expected_role"]:
            mismatches.append(
                {
                    "passage_id": row["passage_id"],
                    "predicted": predicted,
                    "expected": row["expected_role"],
                    "notes": row["notes"],
                }
            )

    allowed_mismatches = int(len(rows) * 0.05)
    assert len(mismatches) <= allowed_mismatches, mismatches


def test_cross_reference_false_positive_rate_stays_below_five_percent() -> None:
    non_cross_reference_rows = [
        row for row in _load_labeled_passages() if row["expected_role"] != "cross_reference"
    ]
    false_positives: list[dict[str, Any]] = []

    for row in non_cross_reference_rows:
        predicted = classify_passage_role(
            text=row["text"],
            heading_path=row["heading_path"],
            passage_type=row["passage_type"],
            char_count=row["char_count"],
            caption_text=row["caption_text"],
        )
        if predicted == "cross_reference":
            false_positives.append(
                {
                    "passage_id": row["passage_id"],
                    "predicted": predicted,
                    "expected": row["expected_role"],
                    "notes": row["notes"],
                }
            )

    false_positive_rate = len(false_positives) / len(non_cross_reference_rows)
    assert false_positive_rate < 0.05, false_positives
