"""Extract <table-wrap> elements from NXML and serialize as GitHub-flavored markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any  # ET.Element type lives in defusedxml; use Any to keep mypy quiet


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    table_id: str
    caption: str
    header: list[str]
    rows: list[list[str]]
    footnotes: str = ""


def _text_or_empty(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(node.itertext()).strip()


def _local_name(node: Any) -> str:
    tag = node.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return str(tag)


def _positive_int_attr(node: Any, name: str) -> int:
    raw = node.get(name, "1")
    try:
        return max(int(raw or "1"), 1)
    except ValueError:
        return 1


def _strip_header_artifacts(text: str) -> str:
    """Strip Bookshelf-style trailing numeric footnote markers from header text."""
    return re.sub(r"\s{2,}\d+\s*$", "", text).strip()


def _parse_rows_from_trs(
    tr_nodes: list[Any],
    *,
    strip_header_artifacts: bool = False,
) -> list[list[str]]:
    """Parse NXML table rows, expanding rowspan and colspan."""
    rows: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}

    for tr in tr_nodes:
        row: list[str] = []
        col_idx = 0
        cells = iter(child for child in tr if _local_name(child) in {"td", "th"})

        while True:
            while col_idx in pending:
                value, remaining = pending[col_idx]
                row.append(value)
                if remaining > 1:
                    pending[col_idx] = (value, remaining - 1)
                else:
                    del pending[col_idx]
                col_idx += 1

            cell = next(cells, None)
            if cell is None:
                break

            value = _text_or_empty(cell)
            if strip_header_artifacts:
                value = _strip_header_artifacts(value)
            colspan = _positive_int_attr(cell, "colspan")
            rowspan = _positive_int_attr(cell, "rowspan")

            for _ in range(colspan):
                row.append(value)
                if rowspan > 1:
                    pending[col_idx] = (value, rowspan - 1)
                col_idx += 1

        while col_idx in pending:
            value, remaining = pending[col_idx]
            row.append(value)
            if remaining > 1:
                pending[col_idx] = (value, remaining - 1)
            else:
                del pending[col_idx]
            col_idx += 1

        rows.append(row)

    return rows


def _flatten_header_rows(header_rows: list[list[str]]) -> list[str]:
    """Flatten expanded grouped header rows into one label per leaf column."""
    if not header_rows:
        return []

    width = max(len(row) for row in header_rows)
    padded_rows = [row + [""] * (width - len(row)) for row in header_rows]
    header: list[str] = []
    for col_idx in range(width):
        parts: list[str] = []
        for row in padded_rows:
            value = row[col_idx].strip()
            if value and value not in parts:
                parts.append(value)
        header.append(" / ".join(parts))
    return header


def parse_rows(table_elem: Any) -> list[list[str]]:
    """Parse NXML table rows, expanding rowspan and colspan."""
    return _parse_rows_from_trs(list(table_elem.findall(".//tr")))


def extract_table(table_wrap: Any, *, ordinal: int) -> ExtractedTable:
    """Extract a single <table-wrap> element."""
    nxml_id = table_wrap.get("id")
    table_id = nxml_id if nxml_id else f"table-{ordinal}"

    cap_node = table_wrap.find("caption")
    caption_parts: list[str] = []
    if cap_node is not None:
        title = cap_node.find("title")
        if title is not None:
            caption_parts.append(_text_or_empty(title))
        for p in cap_node.findall("p"):
            caption_parts.append(_text_or_empty(p))
    caption = " - ".join(c for c in caption_parts if c) or table_id

    table = table_wrap.find("table")
    header: list[str] = []
    rows: list[list[str]] = []
    if table is not None:
        thead = table.find("thead")
        if thead is not None:
            header_rows = _parse_rows_from_trs(
                list(thead.findall("tr")),
                strip_header_artifacts=True,
            )
            header = _flatten_header_rows(header_rows)
        tbody = table.find("tbody")
        if tbody is not None:
            rows = parse_rows(tbody)

    # Capture <table-wrap-foot> footnotes.  These often carry clinical
    # qualifiers ("Click here for ...", abbreviation expansions, study
    # caveats) that are part of the table's meaning.
    foot_node = table_wrap.find("table-wrap-foot")
    foot_parts: list[str] = []
    if foot_node is not None:
        for fn in foot_node.iter():
            local = fn.tag.split("}")[-1] if isinstance(fn.tag, str) and "}" in fn.tag else fn.tag
            if local in ("p", "fn"):
                t = _text_or_empty(fn)
                if t:
                    foot_parts.append(t)
        # Fallback: if no <p>/<fn> children, take all text.
        if not foot_parts:
            t = _text_or_empty(foot_node)
            if t:
                foot_parts.append(t)
    footnotes = "\n".join(foot_parts)

    return ExtractedTable(
        table_id=table_id,
        caption=caption,
        header=header,
        rows=rows,
        footnotes=footnotes,
    )


def render_table_markdown(
    *,
    caption: str,
    header: list[str],
    rows: list[list[str]],
    footnotes: str = "",
) -> str:
    """Render a table as GitHub-flavored markdown (caption + header + rows + foot)."""
    parts: list[str] = [caption, ""]
    if header:
        parts.append("| " + " | ".join(header) + " |")
        parts.append("| " + " | ".join("---" for _ in header) + " |")
    if header:
        for index, row in enumerate(rows, start=1):
            if len(row) != len(header):
                raise ValueError(f"row {index} has {len(row)} cells but header has {len(header)}")
    for row in rows:
        parts.append("| " + " | ".join(row) + " |")
    if footnotes:
        parts.append("")
        parts.append(footnotes)
    return "\n".join(parts)
