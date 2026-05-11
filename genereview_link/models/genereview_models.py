"""Pydantic data models for GeneReview Link.

Defines structured data models for validation and serialization.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from genereview_link.models.sections import SectionName


class GeneReviewSection(BaseModel):
    """Represents a single scraped section of a GeneReview."""

    title: str = Field(description="The original title of the section.")
    content: str = Field(description="The full text content of the section.")
    level: int = Field(default=1, description="Heading level (1-6) indicating hierarchy.")
    subsections: dict[str, "GeneReviewSection"] = Field(
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


class LicenseNotice(BaseModel):
    """License and copyright notice for the GeneReviews data source.

    Returned by the dedicated GET /license endpoint. Kept off per-record
    responses to minimize payload size and downstream context cost — callers
    fetch this once and apply it to all consumed data.
    """

    copyright: str = "(c) 1993-2026 University of Washington"
    terms_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK138602/"
    data_source: str = "NCBI Bookshelf — GeneReviews"
    data_source_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK1116/"
    notes: str = (
        "GeneReviews(R) is a copyrighted resource. Attribute the University of "
        "Washington when redistributing. See terms_url for the full notice."
    )


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
    """

    passage_id: str
    nbk_id: str
    gene_symbols: list[str] = []
    chapter_title: str
    chapter_last_updated: date | None = None
    chapter_section: SectionName
    heading_path: str | None = None
    text: str | None = None
    snippet: str | None = None
    char_count: int
    score_breakdown: ScoreBreakdown


class PassageDetail(BaseModel):
    """Returned by GET /passages/{passage_id}."""

    passage_id: str
    nbk_id: str
    chapter_title: str
    chapter_last_updated: date | None = None
    chapter_section: SectionName
    heading_path: str | None = None
    section_level: int
    chunk_index: int
    text: str
    char_count: int
    gene_symbols: list[str] = []
