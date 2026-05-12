"""Parse one BITS NXML chapter into ChapterRecord + PassageRecord list.

Uses defusedxml.lxml per AGENTS.md. Output is ready for asyncpg COPY.

Content-loss guardrails
-----------------------
GeneReviews carries clinical recommendations inside JATS-NXML elements
beyond plain <p>: notably <list>, <def-list>, and <boxed-text>.  A
2026-05 audit found ~12 MB of clinical prose was being silently dropped
because the section walker only handled <p>, <table-wrap>, <sec>.  The
chunker now uses an explicit tag policy:

- CAPTURE_TAGS:    prose-bearing tags whose text is preserved into passages
- STRUCTURAL_TAGS: tags consumed for heading/labeling, no prose to preserve
- KNOWN_SKIP_TAGS: tags deliberately skipped, each with a stored reason
- Unknown tags with non-trivial text are recorded in the per-chapter
  ChapterIngestAudit so the operator can spot regressions.

See docs/superpowers/specs/2026-05-12-chunker-data-loss-findings.md.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import date

from defusedxml.lxml import fromstring
from lxml import etree

from genereview_link.corpus.canonicalize import canonical_section
from genereview_link.corpus.chunking import DEFAULT_OVERLAP_TOKENS, chunk_section_text
from genereview_link.corpus.nxml_render import render_boxed_text, render_def_list, render_list
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.tables import extract_table, render_table_markdown
from genereview_link.corpus.tokenizer import BGE_NET_CHUNK_TOKENS

# Parser version is included in audit logs and can be bound into the
# corpus_version to invalidate caches on parser-affecting changes.
PARSER_VERSION = "2026-05-12-r1"

# Prose-bearing tags whose text must reach a PassageRecord.
CAPTURE_TAGS: frozenset[str] = frozenset(
    {"p", "table-wrap", "sec", "list", "def-list", "boxed-text"}
)

# Structural tags consumed for heading path / labeling; no body prose.
STRUCTURAL_TAGS: frozenset[str] = frozenset({"title", "label"})

# Tags deliberately skipped; each must have a documented reason.
KNOWN_SKIP_TAGS: dict[str, str] = {
    "ref-list": "bibliographic references; cited via PMID elsewhere",
    "fig": "image element; captions are short and noisy in retrieval",
    "graphic": "image reference; no patient-facing prose",
    "supplementary-material": "external file pointer",
    "xref": "cross-reference marker; rendered via parent itertext",
    "disp-formula": "display formula; rare in GeneReviews",
    "inline-formula": "inline formula; rare in GeneReviews",
    "permissions": "copyright metadata; surfaced via genereview://license",
    "notes": "authoring notes, not patient content",
    "fn-group": "footnote markup; rendered through xref",
    "ack": "acknowledgements section",
    "glossary": "glossary; consider promoting in a future pass",
    "app-group": "appendix group; consider promoting in a future pass",
    "back": "back-matter wrapper (refs/ack); handled at body level",
}


class NxmlParseError(Exception):
    """Raised when an NXML file cannot be parsed at all."""


@dataclass(slots=True)
class ChapterIngestAudit:
    """Per-chapter content-conservation audit.

    Emitted by parse_and_chunk_one alongside the chapter + passages.
    The pipeline logs this at INFO and may persist it for trend analysis.
    """

    nbk_id: str
    parser_version: str
    body_text_chars: int = 0
    captured_text_chars: int = 0
    structural_text_chars: int = 0  # <title>/<label> surfaced via heading_path
    passage_count: int = 0
    list_renders: int = 0
    def_list_renders: int = 0
    boxed_text_renders: int = 0
    skipped_by_tag: dict[str, int] = field(default_factory=dict)
    unknown_tags_with_text: dict[str, int] = field(default_factory=dict)

    @property
    def accounted_chars(self) -> int:
        return (
            self.captured_text_chars
            + self.structural_text_chars
            + sum(self.skipped_by_tag.values())
            + sum(self.unknown_tags_with_text.values())
        )

    @property
    def unaccounted_ratio(self) -> float:
        """Fraction of body text not accounted for."""
        if self.body_text_chars <= 0:
            return 0.0
        delta = self.body_text_chars - self.accounted_chars
        if delta <= 0:
            return 0.0
        return delta / self.body_text_chars

    def as_log_extra(self) -> dict[str, object]:
        return {
            "nbk_id": self.nbk_id,
            "parser_version": self.parser_version,
            "body_text_chars": self.body_text_chars,
            "captured_text_chars": self.captured_text_chars,
            "structural_text_chars": self.structural_text_chars,
            "passage_count": self.passage_count,
            "list_renders": self.list_renders,
            "def_list_renders": self.def_list_renders,
            "boxed_text_renders": self.boxed_text_renders,
            "skipped_by_tag": dict(self.skipped_by_tag),
            "unknown_tags_with_text": dict(self.unknown_tags_with_text),
            "unaccounted_ratio": round(self.unaccounted_ratio, 6),
        }


def parse_and_chunk_one(
    raw_xml: bytes,
    *,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
    max_tokens: int = BGE_NET_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> tuple[ChapterRecord, list[PassageRecord], ChapterIngestAudit]:
    """Parse one BITS book-part NXML and emit chapter + chunked passages.

    Returns (chapter, passages, audit).  The audit reports per-tag
    conservation accounting so callers can fail-loud on silent content
    loss.

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
    # T1 findings (2026-05-12): see docs/superpowers/specs/2026-05-12-task-b1-findings.md
    # for the chapter-date semantics audit. Outcome (a) addressed in Task 16:
    # prefer "updated" (editorial content timestamp) over "revised" (schema metadata).
    _ph = meta.find("pub-history")
    if _ph is not None:
        initial = _parse_pub_date(_ph.find("date[@date-type='created']"))
        _upd = _ph.find("date[@date-type='updated']")
        _rev = _upd if _upd is not None else _ph.find("date[@date-type='revised']")
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

    audit = ChapterIngestAudit(nbk_id=nbk_id, parser_version=PARSER_VERSION)
    passages: list[PassageRecord] = []
    body = chapter_root.find("body")
    if body is not None:
        # Whitespace-normalize body text so the audit is comparing apples
        # to apples vs. captured/skipped chars (which are also computed
        # after normalization in the renderers + _text_len).
        body_text = " ".join(("".join(body.itertext()) or "").split())
        audit.body_text_chars = len(body_text)
        global_chunk = 0
        table_ordinal = 0
        top_sections = body.findall("sec")
        if top_sections:
            for section in top_sections:
                for chunk_passages, next_chunk, next_ordinal in _walk_section(
                    section,
                    nbk_id=nbk_id,
                    ancestor_titles=(),
                    level=1,
                    global_chunk=global_chunk,
                    table_ordinal=table_ordinal,
                    max_tokens=max_tokens,
                    overlap_tokens=overlap_tokens,
                    audit=audit,
                ):
                    passages.extend(chunk_passages)
                    global_chunk = next_chunk
                    table_ordinal = next_ordinal
        else:
            # Chapters like "updates" have a flat <body> with <p>/<list>
            # children and no <sec> wrapper.  Treat the body as an
            # implicit top-level section so its content is not lost.
            for chunk_passages, next_chunk, next_ordinal in _walk_section(
                body,
                nbk_id=nbk_id,
                ancestor_titles=(title,),
                level=1,
                global_chunk=global_chunk,
                table_ordinal=table_ordinal,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                audit=audit,
            ):
                passages.extend(chunk_passages)
                global_chunk = next_chunk
                table_ordinal = next_ordinal
    audit.passage_count = len(passages)
    audit.captured_text_chars = sum(p.char_count for p in passages)
    return chapter, passages, audit


# ---------- helpers ----------


def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    return ("".join(el.itertext()) or "").strip() or None


def _text_len(el: etree._Element | None) -> int:
    """Whitespace-normalized text length for audit accounting.

    Body, captured passages, and skipped/unknown tags all measure
    length post whitespace-normalization so the audit ledger balances.
    """
    if el is None:
        return 0
    return len(" ".join(("".join(el.itertext()) or "").split()))


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
    audit: ChapterIngestAudit,
) -> Generator[tuple[list[PassageRecord], int, int], None, None]:
    """Recursive section walker.

    Yields (passages_for_this_call, next_global_chunk, next_table_ordinal).

    Iterates immediate children in source order so <table-wrap> and
    rendered <list>/<def-list>/<boxed-text> passages stay locally
    interleaved with surrounding narrative.

    Tag policy: see CAPTURE_TAGS / STRUCTURAL_TAGS / KNOWN_SKIP_TAGS at
    module top.  Unknown tags with non-trivial text are recorded in
    ``audit.unknown_tags_with_text`` so operators can spot regressions.
    """
    title_el = section.find("title")
    title = _text(title_el) or "(untitled)"
    titles = (*ancestor_titles, title)
    heading_path = " > ".join(titles)
    canonical = canonical_section(titles[0])

    para_accumulator: list[str] = []

    def _flush_now(
        global_chunk_inner: int, table_ordinal_inner: int
    ) -> Generator[tuple[list[PassageRecord], int, int], None, tuple[int, int]]:
        """Drain para_accumulator into passages and yield them."""
        nonlocal para_accumulator
        if para_accumulator:
            flushed, global_chunk_inner = _flush_paragraphs(
                para_accumulator,
                nbk_id=nbk_id,
                canonical=canonical,
                heading_path=heading_path,
                level=level,
                global_chunk=global_chunk_inner,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            )
            para_accumulator = []
            if flushed:
                yield flushed, global_chunk_inner, table_ordinal_inner
        return global_chunk_inner, table_ordinal_inner

    for child in section:
        tag = child.tag
        if not isinstance(tag, str):
            # Skip comments, processing instructions, etc.
            continue

        # Strip namespace prefix if present.
        local = tag.split("}")[-1] if "}" in tag else tag

        if local == "p":
            text = _text(child)
            if text:
                para_accumulator.append(text)

        elif local in ("list", "def-list", "boxed-text"):
            # Render structured prose-bearing tags into markdown text and
            # append to the accumulator so they share heading_path with
            # surrounding narrative.  These were silently dropped before
            # 2026-05-12; see audit findings doc.
            if local == "list":
                rendered = render_list(child)
                audit.list_renders += 1
            elif local == "def-list":
                rendered = render_def_list(child)
                audit.def_list_renders += 1
            else:  # boxed-text
                rendered = render_boxed_text(child)
                audit.boxed_text_renders += 1
            if rendered:
                para_accumulator.append(rendered)

        elif local == "table-wrap":
            # Flush accumulated paragraphs before emitting the table passage.
            global_chunk, table_ordinal = yield from _flush_now(global_chunk, table_ordinal)

            table_ordinal += 1
            extracted = extract_table(child, ordinal=table_ordinal)
            markdown = render_table_markdown(
                caption=extracted.caption,
                header=extracted.header,
                rows=extracted.rows,
                footnotes=extracted.footnotes,
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
                    "footnotes": extracted.footnotes,
                },
            )
            global_chunk += 1
            yield [table_passage], global_chunk, table_ordinal

        elif local == "sec":
            # Flush accumulated paragraphs before recursing.
            global_chunk, table_ordinal = yield from _flush_now(global_chunk, table_ordinal)

            for sub_passages, sub_next, sub_ordinal in _walk_section(
                child,
                nbk_id=nbk_id,
                ancestor_titles=titles,
                level=level + 1,
                global_chunk=global_chunk,
                table_ordinal=table_ordinal,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                audit=audit,
            ):
                yield sub_passages, sub_next, sub_ordinal
                # Carry running counters forward across siblings.
                global_chunk = sub_next
                table_ordinal = sub_ordinal

        elif local in STRUCTURAL_TAGS:
            # <title> and <label> are consumed for heading_path / bullet
            # labels and carry no body prose at this level.  Count their
            # text in audit.structural_text_chars so the ledger balances.
            audit.structural_text_chars += _text_len(child)

        elif local in KNOWN_SKIP_TAGS:
            # Recorded under skipped_by_tag with documented reason.
            n = _text_len(child)
            if n:
                audit.skipped_by_tag[local] = audit.skipped_by_tag.get(local, 0) + n

        else:
            # Unknown tag.  If it carries text, log it in the audit so the
            # operator can decide whether to add it to CAPTURE_TAGS or
            # KNOWN_SKIP_TAGS.  We do NOT silently ignore.
            n = _text_len(child)
            if n:
                audit.unknown_tags_with_text[local] = audit.unknown_tags_with_text.get(local, 0) + n

    # Flush any remaining paragraph text at end of section.
    global_chunk, table_ordinal = yield from _flush_now(global_chunk, table_ordinal)
