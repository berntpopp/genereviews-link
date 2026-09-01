"""Reviewed migration identity without database-driver imports."""

from __future__ import annotations

EXPECTED_CONTROL_MIGRATIONS = frozenset(
    {
        "0001_base",
        "0002_corpus_version",
        "0003_refresh_log",
        "0004_active_embedding",
        "0005_corpus_source_identity",
        "0006_computation_runs",
        "0007_release_readiness",
    }
)
EXPECTED_DATA_MIGRATIONS = frozenset(
    {
        f"genereview:{version}"
        for version in (
            "0001_chapters",
            "0002_passages",
            "0003_embeddings_bge384",
            "0004_passage_type_and_tables",
            "0005_passage_role",
            "0006_primary_gene_symbols",
            "0007_embedding_run_identity",
        )
    }
)

__all__ = ["EXPECTED_CONTROL_MIGRATIONS", "EXPECTED_DATA_MIGRATIONS"]
