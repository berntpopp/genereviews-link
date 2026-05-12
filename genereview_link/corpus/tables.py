"""Extract <table-wrap> elements from NXML and serialize as GitHub-flavored markdown."""

from __future__ import annotations

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
            header_row = thead.find("tr")
            if header_row is not None:
                header = [_text_or_empty(th) for th in header_row.findall("th")]
        tbody = table.find("tbody")
        if tbody is not None:
            for tr in tbody.findall("tr"):
                rows.append([_text_or_empty(td) for td in tr.findall("td")])

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
    for row in rows:
        # pad rows to header width
        padded = list(row) + [""] * max(0, len(header) - len(row))
        parts.append("| " + " | ".join(padded) + " |")
    if footnotes:
        parts.append("")
        parts.append(footnotes)
    return "\n".join(parts)
