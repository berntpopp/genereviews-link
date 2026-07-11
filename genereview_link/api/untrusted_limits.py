"""Response-wide untrusted-text limit enforcement at the MCP boundary.

Wraps ``enforce_untrusted_text_limits`` so a breach becomes an explicit, typed
``response_too_large`` envelope error (413 -> ``invalid_input`` in the fleet
envelope) instead of a generic ``internal_error``. Callers aggregate EVERY
fenced object one response emits into a single call so the 128-object / 8 MiB
total ceilings bound the actual payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from genereview_link.api.errors import StructuredHTTPException
from genereview_link.mcp.untrusted_content import (
    UntrustedText,
    UntrustedTextLimitError,
    enforce_untrusted_text_limits,
)

# Generous object-count ceiling for tools whose fenced-object count is bounded
# only by one upstream record's size (a wide table, a deep chapter tree) rather
# than a caller ``limit``. The 2 MiB/object and 8 MiB/total BYTE limits remain
# the real DoS backstop and always apply regardless of this value.
GENEROUS_MAX_OBJECTS = 10_000


def collect_untrusted(value: Any) -> list[UntrustedText]:
    """Walk any response value and return every ``UntrustedText`` it contains.

    Recurses through pydantic models, dicts, and sequences so a single
    ``guard_untrusted_limits`` call bounds the WHOLE response's fenced payload
    (text/snippet + fenced table cells + fenced section trees + fenced metadata),
    not one field at a time.
    """
    found: list[UntrustedText] = []
    _walk(value, found)
    return found


def _walk(value: Any, out: list[UntrustedText]) -> None:
    if isinstance(value, UntrustedText):
        out.append(value)
    elif isinstance(value, BaseModel):
        for sub in value.__dict__.values():
            _walk(sub, out)
    elif isinstance(value, dict):
        for sub in value.values():
            _walk(sub, out)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            _walk(sub, out)


def guard_untrusted_limits(
    objects: list[UntrustedText], *, max_objects: int = GENEROUS_MAX_OBJECTS
) -> None:
    """Enforce v1.1 limits over every fenced object in one response.

    Raises a typed ``response_too_large`` ``StructuredHTTPException`` (413) on
    breach so the envelope surfaces an explicit limit error, never a generic
    internal error.
    """
    try:
        enforce_untrusted_text_limits(objects, max_objects=max_objects)
    except UntrustedTextLimitError as exc:
        raise StructuredHTTPException(
            status_code=413,
            code="response_too_large",
            message=str(exc),
            recovery_hint=(
                "narrow the request (fewer sections/rows/passages) so the response "
                "fits the untrusted-text size ceilings"
            ),
        ) from exc
