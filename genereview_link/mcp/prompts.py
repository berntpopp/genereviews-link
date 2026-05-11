"""Canonical workflow prompts surfaced through the MCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from genereview_link.models.sections import SectionName


def find_in_section(gene_symbol: str, section: SectionName) -> str:
    section_human = section.replace("_", " ")
    return (
        f"Find {section_human} guidance for {gene_symbol} carriers in "
        f"GeneReviews. Call search_passages with "
        f"q='{gene_symbol} {section_human}', sections=['{section}'], "
        f"rerank='rrf', mode='brief', limit=5. Pick the top 2-3 most "
        f"relevant hits and call get_passage on each. Cite passage_id "
        f"and chapter NBK ID for every claim. The attribution is in "
        f"_meta.attribution on the search response."
    )


def register_prompts(mcp: FastMCP) -> None:
    """Register all MCP prompts on the supplied FastMCP instance."""
    mcp.prompt(name="find_in_section")(find_in_section)
