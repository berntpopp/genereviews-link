"""Bounded, duplicate-free JSON parsing before recursive decoder entry."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

DEFAULT_MAX_DEPTH = 64
MAX_NUMERIC_TOKEN_CHARACTERS = 128


class StrictJsonError(ValueError):
    """JSON violated the bounded structural parsing contract."""


class _DuplicateKeyError(ValueError):
    pass


class _NonFiniteConstantError(ValueError):
    pass


class _NumericTokenError(ValueError):
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


def _reject_nonfinite_constant(value: str) -> None:
    raise _NonFiniteConstantError(f"non-standard JSON constant: {value}")


def _bounded_integer(value: str) -> int:
    if len(value.lstrip("-")) > MAX_NUMERIC_TOKEN_CHARACTERS:
        raise _NumericTokenError("integer token exceeds the reviewed bound")
    return int(value)


def _bounded_finite_float(value: str) -> float:
    if len(value) > MAX_NUMERIC_TOKEN_CHARACTERS:
        raise _NumericTokenError("floating-point token exceeds the reviewed bound")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _NumericTokenError("floating-point token is not finite")
    return parsed


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
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_nonfinite_constant,
            parse_int=_bounded_integer,
            parse_float=_bounded_finite_float,
        )
    except _NumericTokenError as error:
        raise StrictJsonError("JSON numeric token violates the reviewed finite bound") from error
    except (UnicodeDecodeError, RecursionError, ValueError) as error:
        raise StrictJsonError("JSON input is not strict bounded JSON") from error


__all__ = [
    "MAX_NUMERIC_TOKEN_CHARACTERS",
    "StrictJsonError",
    "load_strict_json",
]
