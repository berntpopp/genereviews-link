"""Pydantic data models for GeneReview Link.

Defines structured data models for validation and serialization.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from genereview_link.models.sections import SectionName


class GeneReviewSection(BaseModel):
    """Represents a single scraped section of a GeneReview."""

    title: str = Field(description="The original title of the section.")
    content: str = Field(description="The full text content of the section.")
    level: int = Field(default=1, description="Heading level (1-6) indicating hierarchy.")
    subsections: dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="Nested subsections."
    )


# Enable forward references for recursive model
GeneReviewSection.model_rebuild()


class SearchResult(BaseModel):
    """Represents search results from NCBI E-utils esearch."""

    count: int = Field(description="Total number of results found.")
    retmax: int = Field(description="Maximum number of results returned.")
    retstart: int = Field(description="Starting position of results.")
    ids: list[str] = Field(description="List of PubMed IDs found.")
    webenv: str = Field(description="Web environment string for history server.")
    querykey: str = Field(description="Query key for history server.")
    corpus_version: str | None = None


class AbstractData(BaseModel):
    """Represents abstract and metadata from PubMed efetch."""

    pmid: str = Field(description="PubMed ID.")
    title: str = Field(description="Article title.")
    abstract: str = Field(description="Article abstract.")
    authors: list[str] = Field(default_factory=list, description="List of author names.")
    journal: str = Field(description="Journal name.")
    publication_date: str = Field(description="Publication date.")
    corpus_version: str | None = None


class LinkData(BaseModel):
    """Represents links from PubMed elink."""

    urls: list[str] = Field(
        default_factory=list,
        description="All available URLs for the publication.",
    )
    corpus_version: str | None = None


class Reference(BaseModel):
    """Represents a single reference."""

    text: str = Field(description="Full reference text.")
    authors: str | None = Field(default=None, description="Extracted author names.")
    title: str | None = Field(default=None, description="Extracted article/book title.")
    journal: str | None = Field(default=None, description="Extracted journal name.")
    year: str | None = Field(default=None, description="Extracted publication year.")
    pmid: str | None = Field(default=None, description="Extracted PubMed ID if present.")


class FullTextMetadata(BaseModel):
    """Metadata extracted from full text."""

    authors: str | None = Field(default=None, description="Author information from the full text.")
    update_info: str | None = Field(default=None, description="Update and posting information.")
    publication_info: str | None = Field(
        default=None, description="Publication and copyright information."
    )
    last_updated: str | None = Field(default=None, description="Last updated date.")
    references: list[str] = Field(default_factory=list, description="List of reference strings.")


class FullTextData(BaseModel):
    """Represents comprehensive scraped content from NCBI Bookshelf."""

    nbk_id: str | None = Field(default=None, description="NCBI Book ID.")
    url: str = Field(description="URL of the scraped page.")
    title: str = Field(description="Title of the document.")
    sections: dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="All scraped sections."
    )
    metadata: FullTextMetadata = Field(
        default_factory=FullTextMetadata, description="Extracted metadata."
    )
    error: str | None = Field(default=None, description="Error message if scraping failed.")
    corpus_version: str | None = None


class GeneReview(BaseModel):
    """Represents the complete structured data for a single GeneReview."""

    gene_symbol: str = Field(description="The gene symbol that was searched.")
    pubmed_id: str = Field(description="The PubMed ID of the GeneReview article.")
    book_url: str = Field(description="The URL to the full GeneReview on the NCBI Bookshelf.")
    title: str = Field(description="The main title of the GeneReview.")
    summary: GeneReviewSection | None = Field(default=None, description="The 'Summary' section.")
    diagnosis: GeneReviewSection | None = Field(
        default=None, description="The 'Diagnosis' section."
    )
    management: GeneReviewSection | None = Field(
        default=None, description="The 'Management' section."
    )
    # This field will hold all other scraped sections dynamically
    other_sections: dict[str, GeneReviewSection] = Field(
        default_factory=dict,
        description="A dictionary of all other scraped sections.",
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
    produces them: RRF rerank produces all four, lexical rerank produces
    lexical fields only, and rerank off produces none.
    """

    passage_id: str
    nbk_id: str
    gene_symbols: list[str] = Field(default_factory=list)
    chapter_title: str
    chapter_last_updated: date | None = None
    chapter_section: SectionName
    heading_path: str | None = None
    passage_type: str = "narrative"
    text: str | None = None
    snippet: str | None = None
    char_count: int
    rrf_score: float | None = None
    lexical_score: float | None = None
    lexical_rank_position: int | None = None
    dense_rank_position: int | None = None
    score_breakdown: ScoreBreakdown | None = None
    heading_path_array: list[str] | None = None
    recommended_citation: str  # always populated; no default to prevent silent omission
    table_id: str | None = None  # populated only when passage_type='table'
    source_url: str  # always populated; chapter-level NCBI Bookshelf URL


class PassageDetail(BaseModel):
    """Returned by GET /passages/{passage_id}."""

    passage_id: str
    nbk_id: str
    chapter_title: str
    chapter_last_updated: date | None = None
    chapter_section: SectionName
    heading_path: str | None = None
    passage_type: str = "narrative"
    section_level: int
    chunk_index: int
    text: str
    char_count: int
    gene_symbols: list[str] = Field(default_factory=list)
    heading_path_array: list[str] | None = None
    recommended_citation: str  # always populated; no default to prevent silent omission
    source_url: str  # always populated; chapter-level NCBI Bookshelf URL


class SearchDiagnosticsModel(BaseModel):
    """Diagnostics emitted under ``_meta.diagnostics`` when a search returns zero results."""

    lexical_hits: int
    lexical_hits_after_filters: int
    applied_filters: list[str]
    suggestions: list[str]


class ResponseMeta(BaseModel):
    """Per-response metadata (attribution, corpus version) emitted under ``_meta``."""

    attribution: str = Field(default=ATTRIBUTION_TEXT)
    corpus_version: str | None = None
    diagnostics: SearchDiagnosticsModel | None = None
    license_summary: str = "Research use only; cite per genereview://license"
    dense_model_id: str | None = None
    embedding_dim: int | None = None


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
    """A passage as returned in a chapter section response."""

    passage_id: str
    heading_path: str | None = None
    section_level: int
    chunk_index: int
    text: str


class ChapterSectionResponse(BaseModel):
    """Envelope returned by GET /chapters/{nbk_id}/sections/{section}."""

    nbk_id: str
    chapter_title: str
    chapter_section: SectionName
    chapter_last_updated: date | None = None
    passages: list[PassageInSection]
    passage_count: int  # always present; equals len(passages)
    concatenated_text: str | None = None  # opt-in via include=concatenated_text
    concatenated_char_count: int | None = None  # only when concatenated_text opted in
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
    caption: str
    section: SectionName
    heading_path: str
    passage_id: str

    model_config = {"populate_by_name": True}


class ChapterMetadataResponse(BaseModel):
    """Envelope returned by GET /chapters/{nbk_id}/metadata."""

    nbk_id: str
    title: str
    chapter_last_updated: date | None = None
    gene_symbols: list[str] = Field(default_factory=list)
    sections: list[SectionSummary] = Field(default_factory=list)
    table_count: int = 0
    tables: list[TableSummary] = Field(default_factory=list)
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}


class TableResponse(BaseModel):
    """Envelope returned by GET /chapters/{nbk_id}/tables/{table_id}."""

    nbk_id: str
    table_id: str
    caption: str
    heading_path: str | None = None
    section: SectionName
    header: list[str]
    rows: list[list[str]]
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
    include: list[Literal["heading_path_array"]] | None = None


class PassageBatchResponse(BaseModel):
    """Response for POST /passages/batch."""

    passages: list[PassageDetail]
    missing_ids: list[str] = Field(default_factory=list)
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}
