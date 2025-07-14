from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class GeneReviewSection(BaseModel):
    """Represents a single scraped section of a GeneReview."""

    title: str = Field(description="The original title of the section.")
    content: str = Field(description="The full text content of the section.")
    level: int = Field(
        default=1, description="Heading level (1-6) indicating hierarchy."
    )
    subsections: Dict[str, "GeneReviewSection"] = Field(
        default_factory=dict, description="Nested subsections."
    )


# Enable forward references for recursive model
GeneReviewSection.model_rebuild()


class SearchResult(BaseModel):
    """Represents search results from NCBI E-utils esearch."""

    count: int = Field(description="Total number of results found.")
    retmax: int = Field(description="Maximum number of results returned.")
    retstart: int = Field(description="Starting position of results.")
    ids: List[str] = Field(description="List of PubMed IDs found.")
    webenv: str = Field(description="Web environment string for history server.")
    querykey: str = Field(description="Query key for history server.")


class AbstractData(BaseModel):
    """Represents abstract and metadata from PubMed efetch."""

    pmid: str = Field(description="PubMed ID.")
    title: str = Field(description="Article title.")
    abstract: str = Field(description="Article abstract.")
    authors: List[str] = Field(
        default_factory=list, description="List of author names."
    )
    journal: str = Field(description="Journal name.")
    publication_date: str = Field(description="Publication date.")


class LinkData(BaseModel):
    """Represents links from PubMed elink."""

    urls: List[str] = Field(
        default_factory=list, description="All available URLs for the publication."
    )


class Reference(BaseModel):
    """Represents a single reference."""

    text: str = Field(description="Full reference text.")
    authors: Optional[str] = Field(None, description="Extracted author names.")
    title: Optional[str] = Field(None, description="Extracted article/book title.")
    journal: Optional[str] = Field(None, description="Extracted journal name.")
    year: Optional[str] = Field(None, description="Extracted publication year.")
    pmid: Optional[str] = Field(None, description="Extracted PubMed ID if present.")


class FullTextMetadata(BaseModel):
    """Metadata extracted from full text."""

    authors: Optional[str] = Field(
        None, description="Author information from the full text."
    )
    update_info: Optional[str] = Field(
        None, description="Update and posting information."
    )
    publication_info: Optional[str] = Field(
        None, description="Publication and copyright information."
    )
    last_updated: Optional[str] = Field(None, description="Last updated date.")
    references: List[str] = Field(
        default_factory=list, description="List of reference strings."
    )


class FullTextData(BaseModel):
    """Represents comprehensive scraped content from NCBI Bookshelf."""

    nbk_id: Optional[str] = Field(None, description="NCBI Book ID.")
    url: str = Field(description="URL of the scraped page.")
    title: str = Field(description="Title of the document.")
    sections: Dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="All scraped sections."
    )
    metadata: FullTextMetadata = Field(
        default_factory=FullTextMetadata, description="Extracted metadata."
    )
    error: Optional[str] = Field(None, description="Error message if scraping failed.")


class GeneReview(BaseModel):
    """Represents the complete structured data for a single GeneReview."""

    gene_symbol: str = Field(description="The gene symbol that was searched.")
    pubmed_id: str = Field(description="The PubMed ID of the GeneReview article.")
    book_url: str = Field(
        description="The URL to the full GeneReview on the NCBI Bookshelf."
    )
    title: str = Field(description="The main title of the GeneReview.")
    summary: Optional[GeneReviewSection] = Field(
        None, description="The 'Summary' section."
    )
    diagnosis: Optional[GeneReviewSection] = Field(
        None, description="The 'Diagnosis' section."
    )
    management: Optional[GeneReviewSection] = Field(
        None, description="The 'Management' section."
    )
    # This field will hold all other scraped sections dynamically
    other_sections: dict[str, GeneReviewSection] = Field(
        default_factory=dict, description="A dictionary of all other scraped sections."
    )
    # Enhanced fields
    abstract_data: Optional[AbstractData] = Field(
        None, description="PubMed abstract and metadata."
    )
    all_links: Optional[LinkData] = Field(
        None, description="All available links from PubMed."
    )
    full_text_data: Optional[FullTextData] = Field(
        None, description="Comprehensive scraped content."
    )
