"""Parse one BITS NXML chapter into ChapterRecord + PassageRecord list.

Uses defusedxml.lxml per AGENTS.md. Output is ready for asyncpg COPY.
"""

from __future__ import annotations

from datetime import date
from typing import Generator, cast

from defusedxml.lxml import fromstring
from lxml import etree

from genereview_link.corpus.canonicalize import canonical_section
from genereview_link.corpus.chunking import DEFAULT_OVERLAP_TOKENS, chunk_section_text
from genereview_link.corpus.records import ChapterRecord, PassageRecord
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

    meta = root.find("book-part-meta")
    if meta is None:
        raise NxmlParseError(f"{nbk_id}: missing <book-part-meta>")

    title_el = meta.find("title-group/title")
    title = _text(title_el) or short_name

    pubmed_id = _text(meta.find("book-part-id[@pub-id-type='pmid']")) or None

    authors = _join_authors(meta.find("contrib-group"))
    initial = _parse_pub_date(meta.find("pub-date[@pub-type='initial']"))
    updated = _parse_pub_date(meta.find("pub-date[@pub-type='updated']"))

    chapter = ChapterRecord(
        nbk_id=nbk_id,
        short_name=short_name,
        title=title,
        pubmed_id=pubmed_id,
        gene_symbols=(),     # populated by sidedata join
        omim_ids=(),         # populated by sidedata join
        authors=authors,
        initial_pub_date=initial,
        last_updated_date=updated,
        nxml_relpath=nxml_relpath,
        raw_metadata={},
    )

    body = root.find("body")
    passages: list[PassageRecord] = []
    if body is not None:
        global_chunk = 0
        for section in body.findall("sec"):
            for chunk_passages, global_chunk in _walk_section(
                section,
                nbk_id=nbk_id,
                ancestor_titles=(),
                level=1,
                global_chunk=global_chunk,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            ):
                passages.extend(chunk_passages)
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


def _walk_section(
    section: etree._Element,
    *,
    nbk_id: str,
    ancestor_titles: tuple[str, ...],
    level: int,
    global_chunk: int,
    max_tokens: int,
    overlap_tokens: int,
) -> Generator[tuple[list[PassageRecord], int], None, None]:
    """Recursive section walker. Yields (passages_for_this_call, next_global_chunk)."""
    title_el = section.find("title")
    title = _text(title_el) or "(untitled)"
    titles = ancestor_titles + (title,)
    heading_path = " > ".join(titles)
    canonical = canonical_section(titles[0])

    own_text_parts = [
        _text(p) for p in section.findall("p") if _text(p)
    ]
    if own_text_parts:
        full = "\n\n".join(cast(list[str], own_text_parts))
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
                    chunk_index=c.chunk_index,
                    text=c.text,
                    char_count=len(c.text),
                    token_estimate=c.token_count,
                )
            )
            global_chunk += 1
        yield passages, global_chunk

    for sub in section.findall("sec"):
        yield from _walk_section(
            sub,
            nbk_id=nbk_id,
            ancestor_titles=titles,
            level=level + 1,
            global_chunk=global_chunk,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
