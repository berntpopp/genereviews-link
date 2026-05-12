"""Unit tests for the parallel-retrieval SQL builder.

These tests don't require a database -- they assert the SQL text shape,
the filter clauses, and the parameter binding contract.
"""
from genereview_link.retrieval.repository import (
    build_dense_candidates_sql,
    build_parallel_search_sql,
)


def test_dense_candidates_sql_includes_filter_clauses():
    _setup, sql, _params = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene="HFE",
        nbk_id=None,
        sections=("management",),
        heading_path_contains=None,
        top_k=200,
    )
    # The dense branch MUST join chapters/passages and apply WHERE filters,
    # not just call HNSW with the embedding query alone.
    assert "join" in sql.lower(), "dense SQL must join passages/chapters when filters present"
    assert "gene_symbols" in sql.lower() or "$" in sql, "gene filter must appear"
    assert "chapter_section" in sql.lower(), "section filter must appear"


def test_dense_candidates_sql_uses_hnsw_iterative_scan():
    setup, _sql, _ = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene=None, nbk_id=None, sections=None, heading_path_contains=None,
        top_k=200,
    )
    # Iterative scan must be configured via setup statements (SET LOCAL), not
    # embedded in the SELECT -- asyncpg prepared statements only support one command.
    assert any("hnsw.iterative_scan" in s for s in setup), (
        "hnsw.iterative_scan must appear in setup statements, not in the SELECT"
    )


def test_dense_candidates_sql_bypasses_hnsw_for_highly_selective_nbk_filter():
    setup, sql, _ = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene=None, nbk_id="NBK1247", sections=None, heading_path_contains=None,
        top_k=200,
    )
    # Single-chapter filter (~30 passages) should use exact cosine over the
    # filtered set, not HNSW. Setup must be empty and SELECT must not contain
    # any hnsw configuration.
    assert setup == [], "HNSW bypass branch must return empty setup statements"
    assert "nbk_id" in sql.lower()
    assert "hnsw" not in sql.lower(), "bypass branch SELECT must not reference hnsw"


def test_parallel_search_sql_returns_union_of_lexical_and_dense():
    sql, _params = build_parallel_search_sql(
        query_text="HFE C282Y", query_vector=[0.0] * 384,
        gene="HFE", nbk_id=None, sections=None, heading_path_contains=None,
        top_k=200,
    )
    assert "union" in sql.lower(), "parallel-retrieval must union lexical and dense"
    assert sql.lower().count("from genereview_passages") >= 2, \
        "parallel-retrieval must have two FROM clauses (one per branch)"
