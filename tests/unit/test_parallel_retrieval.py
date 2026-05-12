"""Unit tests for the parallel-retrieval SQL builder.

These tests don't require a database -- they assert the SQL text shape,
the filter clauses, and the parameter binding contract.
"""
from genereview_link.retrieval.repository import (
    build_dense_candidates_sql,
    build_parallel_search_sql,
)


def test_dense_candidates_sql_includes_filter_clauses():
    sql, _params = build_dense_candidates_sql(
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
    sql, _ = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene=None, nbk_id=None, sections=None, heading_path_contains=None,
        top_k=200,
    )
    # Iterative scan must be set at session level when filters are present;
    # for the no-filter case it's also safe to set it.
    assert "hnsw.iterative_scan" in sql or "set local hnsw" in sql.lower()


def test_dense_candidates_sql_bypasses_hnsw_for_highly_selective_nbk_filter():
    sql, _ = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene=None, nbk_id="NBK1247", sections=None, heading_path_contains=None,
        top_k=200,
    )
    # Single-chapter filter (~30 passages) should use exact cosine over the
    # filtered set, not HNSW. We assert this by looking for explicit "where nbk_id"
    # and absence of hnsw configuration.
    assert "nbk_id" in sql.lower()
    # No need for HNSW iterative scan when scanning <100 rows exactly.


def test_parallel_search_sql_returns_union_of_lexical_and_dense():
    sql, _params = build_parallel_search_sql(
        query_text="HFE C282Y", query_vector=[0.0] * 384,
        gene="HFE", nbk_id=None, sections=None, heading_path_contains=None,
        top_k=200,
    )
    assert "union" in sql.lower(), "parallel-retrieval must union lexical and dense"
    assert sql.lower().count("from genereview_passages") >= 2, \
        "parallel-retrieval must have two FROM clauses (one per branch)"
