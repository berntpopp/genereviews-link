"""Decode the jsonb columns asyncpg hands back as text.

asyncpg has no built-in jsonb codec, and nothing in this package registers one,
so every jsonb column arrives as ``str``. The corpus readers all tested
``isinstance(value, dict)`` and refused what they were given, which is why a
freshly ingested corpus could not reach ``bundle validate`` at all: the row was
complete, and the reader could not see it.

Decoding on the read side keeps the write side exactly as reviewed -- it binds
canonical JSON text with an explicit ``::jsonb`` cast -- so no stored bytes
change shape. Values that are already objects (unit-test rows, or a future
connection that does register a codec) pass through untouched.
"""

from __future__ import annotations

import json


class JsonbColumnError(ValueError):
    """A jsonb column did not decode to the object its reader requires."""


def json_object(value: object, *, label: str) -> dict[str, object]:
    """Return one jsonb column as its object, decoding asyncpg's text form."""
    decoded = value
    if isinstance(decoded, (str, bytes, bytearray)):
        try:
            decoded = json.loads(decoded)
        except ValueError as error:
            raise JsonbColumnError(f"{label} is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise JsonbColumnError(f"{label} is not a JSON object")
    return decoded


def optional_json_object(value: object, *, label: str) -> dict[str, object] | None:
    """Return one nullable jsonb column as its object, or None when it is null."""
    return None if value is None else json_object(value, label=label)


__all__ = ["JsonbColumnError", "json_object", "optional_json_object"]
