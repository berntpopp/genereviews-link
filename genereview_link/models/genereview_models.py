"""Pydantic data models for GeneReview Link.

Defines structured data models for validation and serialization.
"""

from pydantic import BaseModel, Field


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


class AbstractData(BaseModel):
    """Represents abstract and metadata from PubMed efetch."""

    pmid: str = Field(description="PubMed ID.")
    title: str = Field(description="Article title.")
    abstract: str = Field(description="Article abstract.")
    authors: list[str] = Field(default_factory=list, description="List of author names.")
    journal: str = Field(description="Journal name.")
    publication_date: str = Field(description="Publication date.")


class LinkData(BaseModel):
    """Represents links from PubMed elink."""

    urls: list[str] = Field(
        default_factory=list,
        description="All available URLs for the publication.",
    )


class Reference(BaseModel):
    """Represents a single reference."""

    text: str = Field(description="Full reference text.")
    authors: str | None = Field(None, description="Extracted author names.")
    title: str | None = Field(None, description="Extracted article/book title.")
    journal: str | None = Field(None, description="Extracted journal name.")
    year: str | None = Field(None, description="Extracted publication year.")
    pmid: str | None = Field(None, description="Extracted PubMed ID if present.")


class FullTextMetadata(BaseModel):
    """Metadata extracted from full text."""

    authors: str | None = Field(None, description="Author information from the full text.")
    update_info: str | None = Field(None, description="Update and posting information.")
    publication_info: str | None = Field(None, description="Publication and copyright information.")
    last_updated: str | None = Field(None, description="Last updated date.")
    references: list[str] = Field(default_factory=list, description="List of reference strings.")


class FullTextData(BaseModel):
    """Represents comprehensive scraped content from NCBI Bookshelf."""

    nbk_id: str | None = Field(None, description="NCBI Book ID.")
    url: str = Field(description="URL of the scraped page.")
    title: str = Field(description="Title of the document.")
    sections: dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="All scraped sections."
    )
    metadata: FullTextMetadata = Field(
        default_factory=FullTextMetadata, description="Extracted metadata."
    )
    error: str | None = Field(None, description="Error message if scraping failed.")


class GeneReview(BaseModel):
    """Represents the complete structured data for a single GeneReview."""

    gene_symbol: str = Field(description="The gene symbol that was searched.")
    pubmed_id: str = Field(description="The PubMed ID of the GeneReview article.")
    book_url: str = Field(description="The URL to the full GeneReview on the NCBI Bookshelf.")
    title: str = Field(description="The main title of the GeneReview.")
    summary: GeneReviewSection | None = Field(None, description="The 'Summary' section.")
    diagnosis: GeneReviewSection | None = Field(None, description="The 'Diagnosis' section.")
    management: GeneReviewSection | None = Field(None, description="The 'Management' section.")
    # This field will hold all other scraped sections dynamically
    other_sections: dict[str, GeneReviewSection] = Field(
        default_factory=dict,
        description="A dictionary of all other scraped sections.",
    )
    # Enhanced fields
    abstract_data: AbstractData | None = Field(None, description="PubMed abstract and metadata.")
    all_links: LinkData | None = Field(None, description="All available links from PubMed.")
    full_text_data: FullTextData | None = Field(None, description="Comprehensive scraped content.")
