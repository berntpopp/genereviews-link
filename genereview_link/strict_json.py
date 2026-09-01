"""Bounded, duplicate-free JSON parsing before recursive decoder entry."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

DEFAULT_MAX_DEPTH = 64


class StrictJsonError(ValueError):
    """JSON violated the bounded structural parsing contract."""


class _DuplicateKeyError(ValueError):
    pass


def _reject_deep_structure(text: str, *, max_depth: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise StrictJsonError("JSON nesting exceeds the reviewed depth")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise StrictJsonError("JSON structure is invalid")


def _object_without_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def load_strict_json(
    raw: bytes,
    *,
    max_bytes: int,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> object:
    """Decode bounded JSON with finite nesting and unique object keys."""
    if not 0 < len(raw) <= max_bytes:
        raise StrictJsonError("JSON input is outside the reviewed byte bound")
    if max_depth < 1:
        raise StrictJsonError("JSON depth bound is invalid")
    try:
        text = raw.decode("utf-8")
        _reject_deep_structure(text, max_depth=max_depth)
        return json.loads(text, object_pairs_hook=_object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, _DuplicateKeyError) as error:
        raise StrictJsonError("JSON input is not strict bounded JSON") from error


__all__ = ["StrictJsonError", "load_strict_json"]
