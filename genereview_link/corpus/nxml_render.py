"""Render JATS prose-bearing elements that the section walker captures.

The chunker historically only handled <p>, <table-wrap>, <sec>.  A 2026-05
audit found that <list>, <def-list>, and <boxed-text> together carry
~12 MB of clinical prose corpus-wide that was being silently dropped.

This module renders those elements into normalized text that the
paragraph-accumulator in nxml._walk_section can append.  Lists render as
markdown-style bullets so the structure is preserved in the embedding +
search index without inventing new passage types.

See docs/superpowers/specs/2026-05-12-chunker-data-loss-findings.md.
"""

from __future__ import annotations

from lxml import etree


def _local(tag: object) -> str:
    """Strip XML namespace prefix from an element tag."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1] if "}" in tag else tag


def _inline_text(el: etree._Element) -> str:
    """Collapse mixed-content inline text to a single normalized string.

    Whitespace is collapsed to single spaces.  Returns "" for empty.
    """
    raw = "".join(el.itertext()) if el is not None else ""
    # Collapse runs of whitespace (incl. newlines) but keep one space.
    return " ".join(raw.split())


def render_list(el: etree._Element, *, depth: int = 0) -> str:
    """Render a <list> element as bulleted markdown.

    Handles nested <list> inside <list-item> recursively.  <label>
    children (e.g. ordered-list numerals) are preserved as the bullet
    prefix when present.
    """
    if el is None:
        return ""
    indent = "  " * depth
    lines: list[str] = []
    for item in el:
        local = _local(item.tag)
        if local != "list-item":
            continue
        # Optional <label> like "1." or "a.".  When absent, use "-".
        label_el = item.find("label")
        label_txt = _inline_text(label_el) if label_el is not None else ""
        bullet = f"{label_txt} " if label_txt else "- "

        # Gather body content: <p>, nested <list>, free text.
        body_parts: list[str] = []
        nested_lists: list[str] = []
        for child in item:
            c_local = _local(child.tag)
            if c_local == "label":
                continue
            if c_local == "list":
                nested_lists.append(render_list(child, depth=depth + 1))
            elif c_local == "p":
                t = _inline_text(child)
                if t:
                    body_parts.append(t)
            elif c_local == "def-list":
                rendered = render_def_list(child, depth=depth + 1)
                if rendered:
                    nested_lists.append(rendered)
            else:
                # Inline element (xref, italic, bold, …): take its text.
                t = _inline_text(child)
                if t:
                    body_parts.append(t)
        # Some items have text directly on the list-item element.
        direct_text = (item.text or "").strip()
        if direct_text:
            body_parts.insert(0, " ".join(direct_text.split()))
        body = " ".join(p for p in body_parts if p).strip()
        if body:
            lines.append(f"{indent}{bullet}{body}")
        elif nested_lists:
            # Bullet with no inline content (only nested lists).
            lines.append(f"{indent}{bullet}")
        for nested in nested_lists:
            lines.append(nested)
    return "\n".join(lines)


def render_def_list(el: etree._Element, *, depth: int = 0) -> str:
    """Render a <def-list> element as 'term: definition' markdown lines."""
    if el is None:
        return ""
    indent = "  " * depth
    lines: list[str] = []
    for item in el:
        local = _local(item.tag)
        if local != "def-item":
            continue
        term_el = item.find("term")
        def_el = item.find("def")
        term = _inline_text(term_el) if term_el is not None else ""
        # <def> typically wraps <p>; flatten its descendants.
        definition = _inline_text(def_el) if def_el is not None else ""
        if term and definition:
            lines.append(f"{indent}- **{term}**: {definition}")
        elif term:
            lines.append(f"{indent}- **{term}**")
        elif definition:
            lines.append(f"{indent}- {definition}")
    return "\n".join(lines)


def render_boxed_text(el: etree._Element) -> str:
    """Render a <boxed-text> element as a paragraph block.

    Boxed text in GeneReviews carries clinical pearls / notes.  Caption
    (if any) is rendered as a heading prefix.
    """
    if el is None:
        return ""
    parts: list[str] = []
    caption_el = el.find("caption")
    if caption_el is not None:
        cap = _inline_text(caption_el)
        if cap:
            parts.append(f"**{cap}**")
    for child in el:
        local = _local(child.tag)
        if local == "caption":
            continue
        if local == "p":
            t = _inline_text(child)
            if t:
                parts.append(t)
        elif local == "list":
            r = render_list(child)
            if r:
                parts.append(r)
        elif local == "def-list":
            r = render_def_list(child)
            if r:
                parts.append(r)
        else:
            t = _inline_text(child)
            if t:
                parts.append(t)
    return "\n\n".join(parts)
