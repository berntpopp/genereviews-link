"""Table-data enrichment helper for passage responses.

Extracts structured ``header`` / ``rows`` fields from a ``PassageRow`` when
``include=table_data`` is requested. The logic is isolated here so
``passages.py`` stays within its LOC ceiling (see .loc-allowlist) and the
helper is independently unit-testable.

Each header/row cell is upstream table prose, so it is v1.1-fenced
(``untrusted_text``, record_id rooted at ``{nbk_id}#table:{table_id}``). The
former ``markdown_table`` field was dropped — it was an exact rendering of
these now-fenced cells (v1.1 no-duplication).

Width invariant
---------------
``header`` and ``rows`` are only surfaced when every row has the same number
of cells as the header. If *any* row is wider or narrower the structured
fields are left ``None`` — this defends against upstream de-normalisation bugs
(cf. issue #33 which fixed the flattening; we guard here too).
"""

from __future__ import annotations

from typing import Any

from genereview_link.mcp.untrusted_content import UntrustedText, fence_untrusted_text
from genereview_link.retrieval.repository import PassageRow


def fence_table_cells(
    header: list[str],
    rows: list[list[str]],
    *,
    nbk_id: str,
    table_id: str,
) -> tuple[list[UntrustedText], list[list[UntrustedText]]]:
    """Fence every table cell as ``untrusted_text``.

    record_id is rooted at ``{nbk_id}#table:{table_id}`` with a cell-coordinate
    suffix (``#h{j}`` / ``#r{i}c{j}``) for audit precision.
    """
    base = f"{nbk_id}#table:{table_id}"
    fenced_header = [
        fence_untrusted_text(cell, source="genereviews", record_id=f"{base}#h{j}")
        for j, cell in enumerate(header)
    ]
    fenced_rows = [
        [
            fence_untrusted_text(cell, source="genereviews", record_id=f"{base}#r{i}c{j}")
            for j, cell in enumerate(row)
        ]
        for i, row in enumerate(rows)
    ]
    return fenced_header, fenced_rows


def table_fields(
    row: PassageRow,
    *,
    want: bool,
) -> dict[str, Any]:
    """Return ``{"header": ..., "rows": ...}`` (v1.1-fenced cells) for a passage.

    Both values are ``None`` unless:
    - ``want`` is ``True``,
    - ``row.passage_type == "table"``,
    - ``row.table_data`` is a non-empty dict with ``"header"`` and ``"rows"``
      keys, **and**
    - every data row has exactly ``len(header)`` cells (width invariant).

    When the invariant fails both fields are ``None`` to avoid surfacing
    malformed data. The caller can still rely on ``row.text`` (v1.1-fenced),
    which contains the pre-rendered markdown already stored in the DB.
    """
    null: dict[str, Any] = {"header": None, "rows": None}

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

    fenced_header, fenced_rows = fence_table_cells(
        header, rows, nbk_id=row.nbk_id, table_id=row.table_id or row.passage_id
    )
    return {"header": fenced_header, "rows": fenced_rows}
