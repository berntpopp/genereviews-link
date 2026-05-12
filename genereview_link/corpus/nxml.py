"""Parse one BITS NXML chapter into ChapterRecord + PassageRecord list.

Uses defusedxml.lxml per AGENTS.md. Output is ready for asyncpg COPY.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date

from defusedxml.lxml import fromstring
from lxml import etree

from genereview_link.corpus.canonicalize import canonical_section
from genereview_link.corpus.chunking import DEFAULT_OVERLAP_TOKENS, chunk_section_text
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.tables import extract_table, render_table_markdown
from genereview_link.corpus.tokenizer import BGE_NET_CHUNK_TOKENS


class NxmlParseError(Exception):
    """Raised when an NXML file cannot be parsed at all."""


def parse_and_chunk_one(
    raw_xml: bytes,
    *,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
    max_tokens: int = BGE_NET_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> tuple[ChapterRecord, list[PassageRecord]]:
    """Parse one BITS book-part NXML and emit chapter + chunked passages.

    Raises:
        NxmlParseError: if the XML cannot be parsed.
    """
    try:
        root = fromstring(raw_xml)
    except etree.XMLSyntaxError as exc:
        raise NxmlParseError(f"XML syntax error in {nbk_id}: {exc}") from exc

    # Real NCBI NXMLs use <book-part-wrapper> as root with the chapter
    # <book-part> nested inside; the plan's hand-written fixtures use
    # <book-part> as the root. Handle both shapes by searching for the
    # first <book-part-meta> anywhere in the tree.
    chapter_root = root
    if root.find("book-part-meta") is None:
        nested = root.find(".//book-part")
        if nested is not None and nested.find("book-part-meta") is not None:
            chapter_root = nested

    meta = chapter_root.find("book-part-meta")
    if meta is None:
        raise NxmlParseError(f"{nbk_id}: missing <book-part-meta>")

    title_el = meta.find("title-group/title")
    title = _text(title_el) or short_name

    pubmed_id = _text(meta.find("book-part-id[@pub-id-type='pmid']")) or None

    authors = _join_authors(meta.find("contrib-group"))
    # Production NCBI NXMLs (litarch tarball) store dates in
    #   <pub-history><date date-type="created|revised|...">
    # Hand-crafted fixtures use the older BITS pattern:
    #   <pub-date pub-type="initial|updated|last-revision">
    # Probe the production pattern first; fall back to fixtures pattern.
    _ph = meta.find("pub-history")
    if _ph is not None:
        initial = _parse_pub_date(_ph.find("date[@date-type='created']"))
        # "revised" is the most recent revision; fall back to "updated"
        _rev = _ph.find("date[@date-type='revised']") or _ph.find("date[@date-type='updated']")
        updated = _parse_pub_date(_rev)
    else:
        initial = _parse_pub_date(meta.find("pub-date[@pub-type='initial']"))
        _last_rev = meta.find("pub-date[@pub-type='last-revision']")
        _updated_el = meta.find("pub-date[@pub-type='updated']")
        updated = _parse_pub_date(_last_rev if _last_rev is not None else _updated_el)

    chapter = ChapterRecord(
        nbk_id=nbk_id,
        short_name=short_name,
        title=title,
        pubmed_id=pubmed_id,
        gene_symbols=(),  # populated by sidedata join
        omim_ids=(),  # populated by sidedata join
        authors=authors,
        initial_pub_date=initial,
        last_updated_date=updated,
        nxml_relpath=nxml_relpath,
        raw_metadata={},
    )

    body = chapter_root.find("body")
    passages: list[PassageRecord] = []
    if body is not None:
        global_chunk = 0
        table_ordinal = 0
        for section in body.findall("sec"):
            for chunk_passages, next_chunk, next_ordinal in _walk_section(
                section,
                nbk_id=nbk_id,
                ancestor_titles=(),
                level=1,
                global_chunk=global_chunk,
                table_ordinal=table_ordinal,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            ):
                passages.extend(chunk_passages)
                global_chunk = next_chunk
                table_ordinal = next_ordinal
    return chapter, passages


# ---------- helpers ----------


def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    return ("".join(el.itertext()) or "").strip() or None


def _join_authors(group: etree._Element | None) -> str | None:
    if group is None:
        return None
    names: list[str] = []
    for contrib in group.findall("contrib"):
        surname = _text(contrib.find("name/surname"))
        given = _text(contrib.find("name/given-names"))
        if surname and given:
            names.append(f"{surname} {given}")
        elif surname:
            names.append(surname)
    return ", ".join(names) if names else None


def _parse_pub_date(el: etree._Element | None) -> date | None:
    if el is None:
        return None
    try:
        y = int(_text(el.find("year")) or "")
        m = int(_text(el.find("month")) or "1")
        d = int(_text(el.find("day")) or "1")
        return date(y, m, d)
    except (TypeError, ValueError):
        return None


def _flush_paragraphs(
    text_parts: list[str],
    *,
    nbk_id: str,
    canonical: str,
    heading_path: str,
    level: int,
    global_chunk: int,
    max_tokens: int,
    overlap_tokens: int,
) -> tuple[list[PassageRecord], int]:
    """Chunk and emit accumulated paragraph text. Returns (passages, updated_global_chunk)."""
    if not text_parts:
        return [], global_chunk
    full = "\n\n".join(text_parts)
    chunks = chunk_section_text(full, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    passages: list[PassageRecord] = []
    for c in chunks:
        passages.append(
            PassageRecord(
                nbk_id=nbk_id,
                passage_id=f"{nbk_id}:{global_chunk:04d}",
                chapter_section=canonical,
                heading_path=heading_path,
                section_level=level,
                chunk_index=global_chunk,
                text=c.text,
                char_count=len(c.text),
                token_estimate=c.token_count,
            )
        )
        global_chunk += 1
    return passages, global_chunk


def _walk_section(
    section: etree._Element,
    *,
    nbk_id: str,
    ancestor_titles: tuple[str, ...],
    level: int,
    global_chunk: int,
    table_ordinal: int,
    max_tokens: int,
    overlap_tokens: int,
) -> Generator[tuple[list[PassageRecord], int, int], None, None]:
    """Recursive section walker.

    Yields (passages_for_this_call, next_global_chunk, next_table_ordinal).

    Iterates immediate children in source order so that <table-wrap> passages
    are interleaved with narrative passages at the correct chunk_index positions.
    """
    title_el = section.find("title")
    title = _text(title_el) or "(untitled)"
    titles = (*ancestor_titles, title)
    heading_path = " > ".join(titles)
    canonical = canonical_section(titles[0])

    # Walk immediate children in source order, distinguishing <p>, <table-wrap>, <sec>.
    para_accumulator: list[str] = []

    for child in section:
        tag = child.tag
        if not isinstance(tag, str):
            # Skip comments, PIs, etc.
            continue

        # Strip namespace prefix if present
        local = tag.split("}")[-1] if "}" in tag else tag

        if local == "p":
            text = _text(child)
            if text:
                para_accumulator.append(text)

        elif local == "table-wrap":
            # Flush accumulated paragraphs before emitting the table passage.
            if para_accumulator:
                flushed, global_chunk = _flush_paragraphs(
                    para_accumulator,
                    nbk_id=nbk_id,
                    canonical=canonical,
                    heading_path=heading_path,
                    level=level,
                    global_chunk=global_chunk,
                    max_tokens=max_tokens,
                    overlap_tokens=overlap_tokens,
                )
                para_accumulator = []
                if flushed:
                    yield flushed, global_chunk, table_ordinal

            table_ordinal += 1
            extracted = extract_table(child, ordinal=table_ordinal)
            markdown = render_table_markdown(
                caption=extracted.caption,
                header=extracted.header,
                rows=extracted.rows,
            )
            table_passage = PassageRecord(
                nbk_id=nbk_id,
                passage_id=f"{nbk_id}:{global_chunk:04d}",
                chapter_section=canonical,
                heading_path=f"{heading_path} > Table {table_ordinal}",
                section_level=level,
                chunk_index=global_chunk,
                text=markdown,
                char_count=len(markdown),
                token_estimate=len(markdown.split()),
                passage_type="table",
                table_id=extracted.table_id,
                table_data={
                    "caption": extracted.caption,
                    "header": extracted.header,
                    "rows": extracted.rows,
                },
            )
            global_chunk += 1
            yield [table_passage], global_chunk, table_ordinal

        elif local == "sec":
            # Flush accumulated paragraphs before recursing.
            if para_accumulator:
                flushed, global_chunk = _flush_paragraphs(
                    para_accumulator,
                    nbk_id=nbk_id,
                    canonical=canonical,
                    heading_path=heading_path,
                    level=level,
                    global_chunk=global_chunk,
                    max_tokens=max_tokens,
                    overlap_tokens=overlap_tokens,
                )
                para_accumulator = []
                if flushed:
                    yield flushed, global_chunk, table_ordinal

            for sub_passages, sub_next, sub_ordinal in _walk_section(
                child,
                nbk_id=nbk_id,
                ancestor_titles=titles,
                level=level + 1,
                global_chunk=global_chunk,
                table_ordinal=table_ordinal,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            ):
                yield sub_passages, sub_next, sub_ordinal
                # Carry running counters forward across siblings.
                global_chunk = sub_next
                table_ordinal = sub_ordinal

        # Any other child tags (title, label, etc.) are intentionally ignored.

    # Flush any remaining paragraph text at end of section.
    if para_accumulator:
        flushed, global_chunk = _flush_paragraphs(
            para_accumulator,
            nbk_id=nbk_id,
            canonical=canonical,
            heading_path=heading_path,
            level=level,
            global_chunk=global_chunk,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        if flushed:
            yield flushed, global_chunk, table_ordinal
