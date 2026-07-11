"""Tool-schema parameter descriptions expose closed-value choices inline."""

from __future__ import annotations

from fastapi import FastAPI

from genereview_link.config import ServerConfig
from genereview_link.models.sections import SECTION_NAMES
from genereview_link.server_manager import UnifiedServerManager


def _app() -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    return UnifiedServerManager().create_fastapi_app(config)


def _parameter_description(
    app: FastAPI,
    path: str,
    method: str,
    name: str,
) -> str:
    operation = app.openapi()["paths"][path][method]
    parameter = next(p for p in operation["parameters"] if p["name"] == name)
    return str(parameter["description"])


def _operation(app: FastAPI, path: str, method: str) -> dict:
    return dict(app.openapi()["paths"][path][method])


def test_search_passages_description_leads_with_section_affordances() -> None:
    desc = str(_operation(_app(), "/passages/search", "get")["description"])

    assert 'sections=["management"]' in desc


def test_orchestration_route_descriptions_document_fallbacks_and_versions() -> None:
    app = _app()

    search_description = str(_operation(app, "/search/{gene_symbol}", "get")["description"])
    summary_description = str(_operation(app, "/genereview/{gene_symbol}", "get")["description"])
    abstract_description = str(_operation(app, "/abstract/{pmid}", "get")["description"])
    links_description = str(_operation(app, "/links/{pmid}", "get")["description"])
    fulltext_description = str(_operation(app, "/fulltext/{nbk_id}", "get")["description"])

    assert "indexed corpus first" in search_description
    assert "search_passages(gene=<symbol>)" in search_description
    assert "fresh=true" in search_description
    assert "search_passages" in summary_description
    assert "fresh=true" in summary_description
    assert "corpus_version" in summary_description
    assert "always calls live NCBI" in abstract_description
    assert "corpus_version" in abstract_description
    assert "structured errors" in abstract_description
    assert "fresh=true" in abstract_description
    assert "bypass" not in abstract_description.lower()
    assert "always calls live NCBI" in links_description
    assert "categorized/normalized links" in links_description
    assert "corpus-version stamping" in links_description
    assert "fresh=true" in links_description
    assert "bypass" not in links_description.lower()
    assert "live Bookshelf scrape" in fulltext_description
    assert "corpus passage tools" in fulltext_description
    assert "structured errors/version stamping" in fulltext_description
    assert "fresh=true labels" in fulltext_description
    assert "bypass" not in fulltext_description.lower()


def test_live_upstream_tools_fresh_parameter_only_labels_version() -> None:
    app = _app()

    for path, method in (
        ("/abstract/{pmid}", "get"),
        ("/links/{pmid}", "get"),
        ("/fulltext/{nbk_id}", "get"),
    ):
        desc = _parameter_description(app, path, method, "fresh")

        assert "version" in desc
        assert "live:<timestamp>" in desc
        assert "bypass" not in desc.lower()


def test_get_chapter_metadata_summary_leads_with_outline_affordance() -> None:
    summary = str(_operation(_app(), "/chapters/{nbk_id}/metadata", "get")["summary"])

    assert summary.startswith("The chapter outline tool")


def test_get_chapter_section_description_mentions_default_overlap_stripping() -> None:
    desc = str(_operation(_app(), "/chapters/{nbk_id}/sections/{section}", "get")["description"])

    assert "overlap stripped by default" in desc


def test_search_passages_rerank_description_lists_values_inline() -> None:
    desc = _parameter_description(_app(), "/passages/search", "get", "rerank")

    assert '"rrf" (default; reciprocal-rank fusion' in desc
    assert '"lexical" (weighted lexical score' in desc
    assert '"off" (raw repository order' in desc


def test_search_passages_mode_description_lists_values_inline() -> None:
    desc = _parameter_description(_app(), "/passages/search", "get", "mode")

    assert '"brief" (default; snippet + IDs, ~3 KB)' in desc
    assert '"full" (full text)' in desc
    assert (
        '"ids_only" (lean rows: `passage_id` + `rrf_score` + '
        "`lexical_rank_position` + `chapter_section`)"
    ) in desc


def test_search_passages_sections_description_lists_values_inline() -> None:
    desc = _parameter_description(_app(), "/passages/search", "get", "sections")

    for section in SECTION_NAMES:
        assert f'"{section}"' in desc


def test_search_passages_projection_descriptions_list_values_inline() -> None:
    exclude_desc = _parameter_description(_app(), "/passages/search", "get", "exclude")
    include_desc = _parameter_description(_app(), "/passages/search", "get", "include")

    assert '"score_breakdown"' in exclude_desc
    assert '"heading_path"' in exclude_desc
    assert '"score_breakdown"' in include_desc
    assert '"table_data"' in include_desc


def test_get_chapter_section_description_lists_section_values_inline() -> None:
    desc = _parameter_description(_app(), "/chapters/{nbk_id}/sections/{section}", "get", "section")

    for section in SECTION_NAMES:
        assert f'"{section}"' in desc


def test_get_chapter_metadata_summary_mentions_search_passages() -> None:
    summary = str(_operation(_app(), "/chapters/{nbk_id}/metadata", "get")["summary"])
    assert "search_passages(q, nbk_id=" in summary


def test_get_chapter_section_description_mentions_in_section_search() -> None:
    desc = str(_operation(_app(), "/chapters/{nbk_id}/sections/{section}", "get")["description"])
    assert "search_passages(q, nbk_id=" in desc


def test_get_abstract_description_names_value_add() -> None:
    desc = str(_operation(_app(), "/abstract/{pmid}", "get")["description"])
    assert "normalized response shape" in desc
    assert "structured error envelopes" in desc


def test_get_links_description_names_value_add() -> None:
    desc = str(_operation(_app(), "/links/{pmid}", "get")["description"])
    assert "normalized" in desc.lower()
    assert "structured error envelopes" in desc
