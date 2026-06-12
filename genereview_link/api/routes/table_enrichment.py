"""Table-data enrichment helper for passage responses.

Extracts structured ``header`` / ``rows`` / ``markdown_table`` fields from a
``PassageRow`` when ``include=table_data`` is requested.  The logic is isolated
here so ``passages.py`` stays within its 741-LOC ceiling (see .loc-allowlist)
and the helper is independently unit-testable.

Width invariant
---------------
``header`` and ``rows`` are only surfaced when every row has the same number
of cells as the header.  If *any* row is wider or narrower the structured
fields are left ``None`` — this defends against upstream de-normalisation bugs
(cf. issue #33 which fixed the flattening; we guard here too).

``markdown_table`` uses ``render_table_markdown`` which raises ``ValueError``
on width mismatch, so we still attempt the render only after passing the
invariant check.  When invariant fails, ``markdown_table`` is also ``None``.
"""

from __future__ import annotations

from typing import Any

from genereview_link.corpus.tables import render_table_markdown
from genereview_link.retrieval.repository import PassageRow


def table_fields(
    row: PassageRow,
    *,
    want: bool,
) -> dict[str, Any]:
    """Return ``{"header": ..., "rows": ..., "markdown_table": ...}`` for a passage.

    All three values are ``None`` unless:
    - ``want`` is ``True``,
    - ``row.passage_type == "table"``,
    - ``row.table_data`` is a non-empty dict with ``"header"`` and ``"rows"``
      keys, **and**
    - every data row has exactly ``len(header)`` cells (width invariant).

    When the invariant fails all three fields are ``None`` to avoid surfacing
    malformed data.  The caller can still rely on ``row.text`` which contains
    the pre-rendered markdown already stored in the DB.
    """
    null: dict[str, Any] = {"header": None, "rows": None, "markdown_table": None}

    if not want:
        return null
    if row.passage_type != "table":
        return null
    td = row.table_data
    if not td:
        return null

    raw_header = td.get("header")
    raw_rows = td.get("rows")
    if not isinstance(raw_header, list) or not isinstance(raw_rows, list):
        return null

    header: list[str] = [str(c) for c in raw_header]
    rows: list[list[str]] = [[str(c) for c in r] for r in raw_rows]

    # Width invariant: every row must match the header column count.
    n = len(header)
    if any(len(r) != n for r in rows):
        return null

    caption: str = str(td.get("caption", ""))
    try:
        md = render_table_markdown(caption=caption, header=header, rows=rows)
    except ValueError:
        # render_table_markdown raises on width mismatch; belt-and-suspenders.
        return null

    return {"header": header, "rows": rows, "markdown_table": md}
