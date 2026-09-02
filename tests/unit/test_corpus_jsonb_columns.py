"""asyncpg hands jsonb back as text, and the corpus readers must cope.

Nothing in this package registers a jsonb codec, so `source_capture`,
`provenance` and friends arrive as `str`. Every corpus reader tested
`isinstance(value, dict)` and refused the row it had just been given — so a
freshly ingested, fully embedded corpus could not reach `bundle validate` at
all (#147). Fixture rows in the existing tests are dicts, which is exactly why
this was invisible.
"""

from __future__ import annotations

import json

import pytest

from genereview_link.corpus.jsonb import JsonbColumnError, json_object, optional_json_object

CAPTURE = {"format": "genereviews-offline-source-v1", "chapter_ids": ["NBK9999"], "genesis": True}


def test_a_text_column_decodes_to_its_object() -> None:
    assert json_object(json.dumps(CAPTURE), label="source capture") == CAPTURE


def test_an_object_passes_through_untouched() -> None:
    """A future codec, or a unit-test row, must not be double-decoded."""
    assert json_object(dict(CAPTURE), label="source capture") == CAPTURE


def test_bytes_from_a_binary_codec_decode_too() -> None:
    assert json_object(json.dumps(CAPTURE).encode(), label="source capture") == CAPTURE


@pytest.mark.parametrize("value", ["not json", "[1, 2]", "null", "3", None, 7])
def test_anything_that_is_not_a_json_object_is_refused(value: object) -> None:
    with pytest.raises(JsonbColumnError, match="source capture"):
        json_object(value, label="source capture")


def test_a_nullable_column_may_be_absent() -> None:
    assert optional_json_object(None, label="source capture") is None
    assert optional_json_object(json.dumps(CAPTURE), label="source capture") == CAPTURE


def test_a_nullable_column_still_refuses_a_non_object() -> None:
    with pytest.raises(JsonbColumnError, match="source capture"):
        optional_json_object("[]", label="source capture")
