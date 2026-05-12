"""Verify the genereview://usage MCP resource is registered and returns the markdown content."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI


def _build_mcp(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a real FastMCP instance using create_mcp_server.

    We stub out FastMCP.from_fastapi to avoid spinning up full FastAPI
    routing (and its DB/service dependencies), then let create_mcp_server
    run the resource + prompt registration logic against the real FastMCP
    instance that from_fastapi would normally return.
    """
    from fastmcp import FastMCP

    from genereview_link.config import ServerConfig
    from genereview_link.server_manager import UnifiedServerManager

    def patched_from_fastapi(*args: Any, **kwargs: Any) -> FastMCP:
        return FastMCP(name=kwargs.get("name", "test"), instructions=kwargs.get("instructions"))

    monkeypatch.setattr(FastMCP, "from_fastapi", staticmethod(patched_from_fastapi))

    mgr = UnifiedServerManager()
    app = FastAPI()
    mcp: FastMCP = asyncio.run(mgr.create_mcp_server(app, ServerConfig()))
    return mcp


def test_usage_resource_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """genereview://usage must appear in the MCP resource list."""
    mcp = _build_mcp(monkeypatch)
    resources = asyncio.run(mcp.list_resources())
    uris = [str(r.uri) for r in resources]
    assert "genereview://usage" in uris, f"Expected genereview://usage in {uris}"


def test_usage_resource_content_has_expected_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage resource content must contain all required headings."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    for heading in (
        "# GeneReview-Link Usage Guide",
        "## Pipeline",
        "## Filters",
        "## Rerank modes",
        "### Passage roles",
        "### Query-intent boosts",
        "## Response modes",
        "## `snippet_chars` (brief mode only)",
        "## Diagnostics",
        "## Batch fetch",
        "## Affordances on existing tools",
        "## Table ID naming",
        "## Chapter date semantics",
        "## Latency profile",
    ):
        assert heading in USAGE_RESOURCE_MARKDOWN, f"Missing heading: {heading}"


def test_usage_resource_documents_search_score_and_diagnostic_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document the score visibility and diagnostics contract."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        "`lexical_score`, `lexical_rank_position`, `dense_rank_position`, "
        "`rrf_score`, and `passage_role` as top-level fields",
        "Dense-derived fields (`dense_rank_position`, `rrf_score`, and `adjusted_score`) "
        "are non-null only when dense scores are available and RRF is active",
        "they can be `null` on lexical, off, or RRF fallback paths",
        "Active RRF results are sorted by role- and intent-aware `adjusted_score`",
        "add `include=score_breakdown` to see `adjusted_score`, `role_multiplier`, "
        "`intent_section_boost`",
        "`lexical_candidate_count` and `dense_candidate_count` are post-filter",
        "`dense_candidate_count` is `null` when dense retrieval is not run",
        "`unfiltered_lexical_count` is normally `null`",
        "populated on empty filtered responses after the second unfiltered lexical probe",
        "A non-zero `unfiltered_lexical_count` plus suggestion codes indicates filters "
        "likely dropped candidates",
        '"section-filter-drops-all"',
        "`gene-filter-drops-all`, `broaden-query`, `section-filter-drops-all`, "
        "`nbk-id-filter-drops-all`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_documents_chapter_date_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document current and legacy last-updated parsing."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        '`<date date-type="updated">`',
        '`<date date-type="revised">`',
        '`<pub-date pub-type="last-revision">`',
        '`<pub-date pub-type="updated">`',
        "Chapters with none of those update/revision dates have `chapter_last_updated = null`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_documents_passage_roles_and_query_intent_boosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document ranking roles and inferred query intents."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        "`evidence` (1.0), `cross_reference` (0.4), `definition` (0.95), "
        "`table_caption` (0.85), and `table_body` (1.0)",
        "The role multiplier affects `adjusted_score`",
        "`management` (treatment, management, therapy, surgery, prophylactic, "
        "risk-reducing, screening, surveillance, intervention, prevent, prevention, "
        "managing) boosts `management` by 0.30",
        "`diagnosis` (diagnosis, diagnostic criteria, establishing, confirming, "
        "differential, differential diagnosis) boosts `diagnosis` by 0.30 and "
        "`clinical_features` by 0.10",
        "`genetics` (inheritance, penetrance, autosomal, x-linked, variant spectrum, "
        "molecular genetics) boosts `molecular_genetics` by 0.20 and "
        "`genetic_counseling` by 0.05",
        "server-inferred, not user-tunable",
        "`_meta.diagnostics.query_intents`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_documents_ids_only_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage resource must document the ids_only lean row fields."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected = "{passage_id, nbk_id, rrf_score, lexical_rank_position, chapter_section}"
    assert expected in normalized
    assert "Role-affected `adjusted_score` is not emitted in this mode" in normalized


def test_usage_resource_documents_dedupe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage resource must document default section-text dedupe behaviour."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected = (
        '`include=["concatenated_text"]` returns joined text with chunk overlap '
        "stripped by default. Pass `dedupe=false` only for corpus-auditing workflows "
        "that need literal stored chunk text."
    )
    assert expected in normalized


def test_usage_resource_documents_search_aliases_and_heading_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document query aliases and heading path filtering."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        "`search_passages` accepts both `q` and `query`",
        "`get_passages_batch(ids=[...])`",
        '`sections=["management"]`',
        '`heading_path_contains="Prevention"`',
        "Omit both and the API returns a structured 422",
        "`missing_query`",
        "Providing both with different values returns a structured 422",
        "`conflicting_query_param`",
        "`heading_path_contains` on `search_passages`",
        "`heading_path_contains` also applies to `get_chapter_section`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_documents_lexical_variant_query_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must warn that lexical mode is for narrow variant strings."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        'For variant nomenclature queries in `rerank="lexical"`',
        'prefer the variant token alone, for example `q="c.5266dupC"`',
        'use default `rerank="rrf"` for multi-token clinical questions',
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_resource for genereview://usage must return the markdown string."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    mcp = _build_mcp(monkeypatch)
    result = asyncio.run(mcp.read_resource("genereview://usage"))
    assert result.contents, "read_resource returned empty contents"
    first = result.contents[0]
    raw = first.content if hasattr(first, "content") else str(first)
    assert raw == USAGE_RESOURCE_MARKDOWN


def test_server_instructions_manifests_both_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """instructions= must reference both resource URIs and fit within the 1000-char budget."""
    mcp = _build_mcp(monkeypatch)
    instr = mcp.instructions or ""
    assert "genereview://license" in instr
    assert "genereview://usage" in instr
    # Length check — the whole point of Task 15 is to keep instructions tight.
    assert len(instr) < 1000, f"instructions length {len(instr)} exceeds 1000-char budget"
