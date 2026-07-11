"""Pydantic data models for GeneReview Link.

Defines structured data models for validation and serialization.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)

from genereview_link.mcp.untrusted_content import UntrustedText
from genereview_link.models.sections import SectionName


class GeneReviewSection(BaseModel):
    """A scraped section of a GeneReview (internal; content stays str)."""

    title: str = Field(description="The original title of the section.")
    content: str = Field(description="The full text content of the section.")
    level: int = Field(default=1, description="Heading level (1-6) indicating hierarchy.")
    subsections: dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="Nested subsections."
    )


GeneReviewSection.model_rebuild()  # enable forward refs for the recursive model


class FencedGeneReviewSection(BaseModel):
    """MCP-facing sibling of GeneReviewSection: title + content v1.1-fenced.

    ``_raw_content`` (private, never serialized) carries the ORIGINAL
    pre-normalization upstream text so get_genereview_summary truncation slices
    the RAW bytes and fences THAT (raw_sha256 stays over true raw bytes).
    """

    title: UntrustedText = Field(description="The section's v1.1-fenced heading text.")
    content: UntrustedText = Field(description="The section's v1.1-fenced full text content.")
    level: int = Field(default=1, description="Heading level (1-6) indicating hierarchy.")
    subsections: dict[str, FencedGeneReviewSection] = Field(
        default_factory=dict, description="Nested subsections."
    )
    _raw_content: str = PrivateAttr(default="")


FencedGeneReviewSection.model_rebuild()


class SearchResult(BaseModel):
    """Represents search results from NCBI E-utils esearch."""

    count: int = Field(description="Total number of results found.")
    retmax: int = Field(description="Maximum number of results returned.")
    retstart: int = Field(description="Starting position of results.")
    ids: list[str] = Field(description="List of PubMed IDs found.")
    webenv: str = Field(description="Web environment string for history server.")
    querykey: str = Field(description="Query key for history server.")
    recovery_hint: str | None = Field(
        default=None,
        description="Agent recovery hint emitted only when the search result is empty.",
    )
    corpus_version: str | None = None
    meta: ResponseMeta = Field(
        alias="_meta", default_factory=lambda: ResponseMeta.live_passthrough()
    )
    model_config = {"populate_by_name": True}

    @model_serializer(mode="wrap")
    def _drop_null_recovery_hint(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("recovery_hint") is None:
            data.pop("recovery_hint", None)
        return data


class AbstractData(BaseModel):
    """Represents abstract and metadata from PubMed efetch."""

    pmid: str = Field(description="PubMed ID.")
    title: UntrustedText = Field(description="Article title (v1.1 fenced).")
    abstract: UntrustedText = Field(
        description="Article abstract, v1.1-fenced: upstream prose typed as structural data."
    )
    authors: list[UntrustedText] = Field(
        default_factory=list, description="Author names (v1.1 fenced)."
    )
    journal: UntrustedText = Field(description="Journal name (v1.1 fenced).")
    publication_date: str = Field(description="Publication date.")
    corpus_version: str | None = None
    meta: ResponseMeta = Field(
        alias="_meta", default_factory=lambda: ResponseMeta.live_passthrough()
    )
    model_config = {"populate_by_name": True}


class LinkEntry(BaseModel):
    """A categorized URL returned by PubMed ELink."""

    url: str
    link_type: Literal["prlinks", "llinks", "books", "pmc"]
    # provider is upstream NCBI Provider/Name (or Category) prose -> v1.1 fenced.
    provider: UntrustedText | None = None


class LinkData(BaseModel):
    """Represents links from PubMed elink."""

    urls: list[str] = Field(
        default_factory=list,
        description="All available URLs for the publication.",
    )
    link_entries: list[LinkEntry] | None = None
    by_type: dict[str, list[str]] = Field(default_factory=dict)
    corpus_version: str | None = None
    meta: ResponseMeta = Field(
        alias="_meta", default_factory=lambda: ResponseMeta.live_passthrough()
    )
    model_config = {"populate_by_name": True}


class Reference(BaseModel):
    """Represents a single reference."""

    text: str = Field(description="Full reference text.")
    authors: str | None = Field(default=None, description="Extracted author names.")
    title: str | None = Field(default=None, description="Extracted article/book title.")
    journal: str | None = Field(default=None, description="Extracted journal name.")
    year: str | None = Field(default=None, description="Extracted publication year.")
    pmid: str | None = Field(default=None, description="Extracted PubMed ID if present.")


class FullTextMetadata(BaseModel):
    """Metadata extracted from full text (v1.1 fenced: live-scraped Bookshelf prose)."""

    authors: UntrustedText | None = Field(default=None, description="Author info (v1.1 fenced).")
    update_info: UntrustedText | None = Field(
        default=None, description="Update info (v1.1 fenced)."
    )
    publication_info: UntrustedText | None = Field(
        default=None, description="Publication/copyright info (v1.1 fenced)."
    )
    last_updated: str | None = Field(default=None, description="Last updated date.")
    references: list[UntrustedText] = Field(
        default_factory=list, description="Reference strings (v1.1 fenced)."
    )


class FullTextData(BaseModel):
    """Represents comprehensive scraped content from NCBI Bookshelf."""

    nbk_id: str | None = Field(default=None, description="NCBI Book ID.")
    url: str = Field(description="URL of the scraped page.")
    # v1.1 fenced; None in get_genereview_summary (title deduped to GeneReview.title).
    title: UntrustedText | None = Field(default=None, description="Document title.")
    sections: dict[str, FencedGeneReviewSection] = Field(
        default_factory=dict, description="All scraped sections (v1.1 fenced)."
    )
    metadata: FullTextMetadata = Field(
        default_factory=FullTextMetadata, description="Extracted metadata."
    )
    error: str | None = Field(default=None, description="Error message if scraping failed.")
    corpus_version: str | None = None
    meta: ResponseMeta = Field(
        alias="_meta", default_factory=lambda: ResponseMeta.live_passthrough()
    )
    model_config = {"populate_by_name": True}


class GeneReview(BaseModel):
    """Represents the complete structured data for a single GeneReview."""

    gene_symbol: str = Field(description="The gene symbol that was searched.")
    pubmed_id: str = Field(description="The PubMed ID of the GeneReview article.")
    book_url: str = Field(description="The URL to the full GeneReview on the NCBI Bookshelf.")
    title: UntrustedText = Field(description="The main title of the GeneReview (v1.1 fenced).")
    summary: FencedGeneReviewSection | None = Field(default=None, description="Summary (v1.1).")
    diagnosis: FencedGeneReviewSection | None = Field(default=None, description="Diagnosis (v1.1).")
    management: FencedGeneReviewSection | None = Field(
        default=None, description="Management (v1.1)."
    )
    other_sections: dict[str, FencedGeneReviewSection] = Field(
        default_factory=dict, description="All other scraped sections (v1.1 fenced content)."
    )
    # Enhanced fields
    abstract_data: AbstractData | None = Field(
        default=None, description="PubMed abstract and metadata."
    )
    all_links: LinkData | None = Field(default=None, description="All available links from PubMed.")
    full_text_data: FullTextData | None = Field(
        default=None, description="Comprehensive scraped content."
    )
    corpus_version: str | None = None
    meta: ResponseMeta = Field(
        alias="_meta", default_factory=lambda: ResponseMeta.live_passthrough()
    )
    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Phase 5 new models
# ---------------------------------------------------------------------------


class CorpusVersion(BaseModel):
    """Describes an active corpus version in the Postgres store."""

    version: str
    last_updated: datetime | None = None
    is_active: bool


COPYRIGHT_LINE = "© 1993–present University of Washington"  # noqa: RUF001 — canonical typography

ATTRIBUTION_TEXT = (
    f"GeneReviews® content {COPYRIGHT_LINE}; "
    "sourced from NCBI Bookshelf. Full terms via the genereview://license resource."
)

ATTRIBUTION_TEXT_FULL = (
    "GeneReviews® content © 1993–present University of Washington; "  # noqa: RUF001
    "sourced from NCBI Bookshelf — GeneReviews. "
    "Cite per https://www.ncbi.nlm.nih.gov/books/NBK138602/."
)

PassageRole = Literal["evidence", "cross_reference", "definition", "table_caption", "table_body"]


class LicenseNotice(BaseModel):
    """License and copyright notice for the GeneReviews data source.

    Returned by the dedicated GET /license endpoint. Kept off per-record
    responses to minimize payload size and downstream context cost — callers
    fetch this once and apply it to all consumed data.
    """

    copyright: str = COPYRIGHT_LINE
    terms_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK138602/"
    data_source: str = "NCBI Bookshelf — GeneReviews"
    data_source_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK1116/"
    notes: str = (
        "GeneReviews(R) is a copyrighted resource. Attribute the University of "
        "Washington when redistributing. See terms_url for the full notice."
    )
    license_spdx: str = "LicenseRef-GeneReviews"
    attribution_text: str = ATTRIBUTION_TEXT_FULL


class ScoreBreakdown(BaseModel):
    """Per-passage ranking scores produced by the retrieval pipeline."""

    lexical_rank: float
    phrase_rank: float
    strict_rank: float
    recall_rank: float
    adjusted_score: float | None = None
    role_multiplier: float = 1.0
    intent_section_boost: float = 0.0
    passage_role: PassageRole | None = None
    dense_score: float | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    section_priority: int
    final_position: int


class RankedPassage(BaseModel):
    """A passage returned by /passages/search, annotated with ranking scores.

    Either ``text`` or ``snippet`` is populated, never both. The route's
    ``mode`` query parameter controls which:
    - ``mode="brief"`` (default) → ``snippet`` populated, ``text`` null.
    - ``mode="full"`` → ``text`` populated, ``snippet`` null.

    Top-level rank fields are populated whenever the selected rerank mode
    produces them: RRF rerank produces all four when dense scores are available,
    lexical rerank produces lexical fields, and rerank off still exposes the
    repository lexical score while position/RRF/dense fields remain null.
    """

    passage_id: str
    nbk_id: str
    gene_symbols: list[str] = Field(default_factory=list)
    chapter_title: UntrustedText  # upstream chapter title prose, v1.1 fenced
    chapter_last_updated: date | None = None
    chapter_ingested_at: datetime | None = None
    chapter_section: SectionName
    heading_path: UntrustedText | None = None  # upstream heading text, v1.1 fenced
    passage_type: str = "narrative"
    passage_role: PassageRole | None = None
    text: UntrustedText | None = None
    snippet: UntrustedText | None = None
    char_count: int
    rrf_score: float | None = None
    lexical_score: float | None = None
    lexical_rank_position: int | None = None
    dense_rank_position: int | None = None
    score_breakdown: ScoreBreakdown | None = None
    # identifiers/date only (no prose); the title is fenced on chapter_title.
    recommended_citation: str  # always populated; no default to prevent silent omission
    table_id: str | None = None  # populated only when passage_type='table'
    source_url: str  # always populated; chapter-level NCBI Bookshelf URL
    # Opt-in table cells (include=table_data), v1.1-fenced; markdown_table dropped.
    header: list[UntrustedText] | None = None
    rows: list[list[UntrustedText]] | None = None


class PassageDetail(BaseModel):
    """Returned by GET /passages/{passage_id}."""

    passage_id: str
    nbk_id: str
    chapter_title: UntrustedText  # upstream chapter title prose, v1.1 fenced
    chapter_last_updated: date | None = None
    chapter_section: SectionName
    heading_path: UntrustedText | None = None  # upstream heading text, v1.1 fenced
    passage_type: str = "narrative"
    passage_role: PassageRole | None = None
    section_level: int
    chunk_index: int
    text: UntrustedText
    char_count: int
    gene_symbols: list[str] = Field(default_factory=list)
    # identifiers/date only (no prose); the title is fenced on chapter_title.
    recommended_citation: str  # always populated; no default to prevent silent omission
    source_url: str  # always populated; chapter-level NCBI Bookshelf URL
    # Opt-in table cells (include=table_data), v1.1-fenced; markdown_table dropped.
    header: list[UntrustedText] | None = None
    rows: list[list[UntrustedText]] | None = None


class SearchDiagnosticsModel(BaseModel):
    """Diagnostics emitted under ``_meta.diagnostics`` for every search response."""

    rerank_used: Literal["rrf", "lexical", "off"]
    lexical_candidate_count: int
    dense_candidate_count: int | None = None
    section_filters: list[str] = Field(default_factory=list)
    unfiltered_lexical_count: int | None = None
    applied_filters: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    query_intents: list[str] = Field(default_factory=list)


class ResponseMeta(BaseModel):
    """Per-response metadata (attribution, corpus version, affordance hints) emitted under ``_meta``.

    ``next_commands`` is the canonical location for agentic-affordance hints across
    all SUCCESS responses (errors carry their own ``next_commands`` in the
    StructuredHTTPException envelope). It defaults to None and is stripped from
    the JSON output when null, so callers see the field ONLY when a hint is
    actually present.
    """

    attribution: str = Field(default=ATTRIBUTION_TEXT)
    corpus_version: str | None = None
    diagnostics: SearchDiagnosticsModel | None = None
    license_summary: str = "Research use only; cite per genereview://license"
    dense_model_id: str | None = None
    embedding_dim: int | None = None
    truncated: bool = False
    next_commands: list[dict[str, Any]] | None = None

    @model_serializer(mode="wrap")
    def _drop_null_next_commands(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Strip ``next_commands`` when it is None so the key is absent.

        Other None-valued fields (notably ``corpus_version``) are preserved as
        ``null`` because callers and existing tests rely on their presence as a
        signal. ``next_commands`` is different: it is a hint, and a ``null``
        sentinel is indistinguishable from "no hint" only when callers check
        truthiness; clients that key-check would misinterpret a null as an
        actionable affordance.
        """
        data: dict[str, Any] = handler(self)
        if data.get("next_commands") is None:
            data.pop("next_commands", None)
        return data

    @classmethod
    def live_passthrough(cls) -> ResponseMeta:
        """Metadata for live NCBI passthrough responses not tied to an indexed corpus."""
        return cls(corpus_version=None)


AbstractData.model_rebuild()
LinkData.model_rebuild()
FullTextData.model_rebuild()
GeneReview.model_rebuild()


class IdsOnlyPassage(BaseModel):
    """Lean row shape for search_passages(mode='ids_only')."""

    passage_id: str
    nbk_id: str
    chapter_section: SectionName
    rrf_score: float | None = None
    lexical_rank_position: int | None = None


class IdsOnlySearchResponse(BaseModel):
    """Envelope returned by GET /passages/search when mode=ids_only."""

    results: list[IdsOnlyPassage]
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)
    model_config = {"populate_by_name": True}


class PassageSearchResponse(BaseModel):
    """Envelope returned by GET /passages/search."""

    results: list[RankedPassage]
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)
    model_config = {"populate_by_name": True}


class PassageWindowResponse(BaseModel):
    """Response shape for /passages/{id} (always wrapped, even when neighbors=0)."""

    passage: PassageDetail
    neighbors_before: list[PassageDetail] = Field(default_factory=list)
    neighbors_after: list[PassageDetail] = Field(default_factory=list)
    has_more_before: bool = False
    has_more_after: bool = False
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}


class PassageInSection(BaseModel):
    """A passage's structural id within a chapter section response.

    ``text`` is deliberately absent (v1.1 no-duplication): the section's
    prose lives once, fenced, on ``ChapterSectionResponse.content``.
    """

    passage_id: str
    heading_path: UntrustedText | None = None  # upstream heading text, v1.1 fenced
    section_level: int
    chunk_index: int


class ChapterSectionResponse(BaseModel):
    """Envelope for GET /chapters/{nbk_id}/sections/{section}; content is v1.1-fenced."""

    nbk_id: str
    chapter_title: UntrustedText  # upstream chapter title prose, v1.1 fenced
    chapter_section: SectionName
    chapter_last_updated: date | None = None
    passages: list[PassageInSection]
    passage_count: int  # always present; equals len(passages)
    content: UntrustedText
    content_char_count: int
    note: str | None = None
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)
    model_config = {"populate_by_name": True}


class SectionSummary(BaseModel):
    """Per-section passage count and char count, emitted inside ChapterMetadataResponse."""

    section: SectionName
    passage_count: int
    total_char_count: int
    note: str | None = None


class TableSummary(BaseModel):
    """One table on a chapter: canonical slug, caption, section + heading context."""

    table_id: str
    caption: UntrustedText  # upstream table caption prose, v1.1 fenced
    section: SectionName
    heading_path: UntrustedText  # upstream heading text, v1.1 fenced
    passage_id: str

    model_config = {"populate_by_name": True}


class ChapterMetadataResponse(BaseModel):
    """Envelope returned by GET /chapters/{nbk_id}/metadata."""

    nbk_id: str
    title: UntrustedText  # upstream chapter title prose, v1.1 fenced
    chapter_last_updated: date | None = None
    chapter_ingested_at: datetime | None = None
    gene_symbols: list[str] = Field(default_factory=list)
    sections: list[SectionSummary] = Field(default_factory=list)
    table_count: int = 0
    tables: list[TableSummary] = Field(default_factory=list)
    # Staleness / token-estimate fields (computed at response time, #46 / #40)
    years_since_update: float | None = None
    staleness_band: Literal["current", "aging", "stale", "very_stale"] | None = None
    likely_stale_for_therapeutics: bool = False
    total_char_count: int = 0
    total_tokens_estimate: int = 0
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}


class TableResponse(BaseModel):
    """GET /chapters/{nbk_id}/tables/{table_id}; caption + every cell v1.1-fenced.

    The former ``markdown_table`` field + ``format`` query param were dropped:
    markdown duplicated the now-fenced caption/header/rows (v1.1 no-duplication).
    """

    nbk_id: str
    table_id: str
    caption: UntrustedText
    heading_path: UntrustedText | None = None  # upstream heading text, v1.1 fenced
    section: SectionName
    header: list[UntrustedText]
    rows: list[list[UntrustedText]]
    passage_id: str
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Pass-3-A batch models (Task 8)
# ---------------------------------------------------------------------------


class PassageBatchRequest(BaseModel):
    """Body for POST /passages/batch."""

    ids: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^NBK\d+:\d{4}$")]],
        Field(min_length=1),
    ]
    include: list[Literal["table_data"]] | None = None


class PassageBatchResponse(BaseModel):
    """Response for POST /passages/batch."""

    passages: list[PassageDetail]
    missing_ids: list[str] = Field(default_factory=list)
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Search-batch models (Issue #45)
# ---------------------------------------------------------------------------


class SearchBatchSpec(BaseModel):
    """One search spec in a batch request; mirrors GET /passages/search params."""

    q: Annotated[str, Field(min_length=1, max_length=512)]
    gene: str | None = None
    nbk_id: str | None = None
    sections: list[SectionName] | None = None
    heading_path_contains: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    mode: Literal["brief", "full", "ids_only"] = "brief"
    limit: Annotated[int, Field(ge=1, le=100)] = 5
    rerank: Literal["rrf", "lexical", "off"] = "rrf"
    snippet_chars: Annotated[int, Field(ge=80, le=800)] = 400


class SearchBatchRequest(BaseModel):
    """Body for POST /passages/search/batch — 1-5 independent search specs."""

    specs: Annotated[list[SearchBatchSpec], Field(min_length=1, max_length=5)]


class SearchBatchResultItem(BaseModel):
    """One result in a batch search response.

    ``query_index`` matches the spec's zero-based position in the request.
    ``hits`` mirrors GET /passages/search results for that spec's ``mode``.
    Deduplication: when a passage_id appears in multiple results, every
    non-canonical occurrence (query_index > lowest) carries
    ``also_matched_query_indices`` listing the other matching indices.
    Hits are never removed; the annotation signals redundancy only.
    """

    query_index: int
    q: str
    sections: list[SectionName] | None = None
    hits: list[Any] = Field(default_factory=list)


class SearchBatchResponse(BaseModel):
    """Envelope returned by POST /passages/search/batch."""

    results: list[SearchBatchResultItem]
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}
