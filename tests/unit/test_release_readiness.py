"""Last-written release-readiness contract tests."""

from __future__ import annotations

from importlib import resources

import pytest

from genereview_link.corpus.readiness import ReadinessError, build_readiness_payload


def _manifest() -> dict[str, object]:
    return {
        "manifest_version": "3",
        "corpus_release_id": "2026-08-30-r1",
        "corpus_version": "2026-08-30",
        "tarball_source_sha256": "1" * 64,
        "chapter_count": 882,
        "passage_count": 12_345,
        "embedding": {"count": 12_345},
        "schema_migrations": {
            "control": ["0001_base", "0007_release_readiness"],
            "data": ["genereview:0001_chapters"],
        },
        "hnsw": {
            "index_name": "genereview_embeddings_bge384_hnsw_cosine",
            "exists": True,
        },
        "evaluation": {"result_sha256": "2" * 64},
        "checksums": {"corpus.dump": "3" * 64},
    }


def test_readiness_payload_is_exact_three_volume_last_written_contract() -> None:
    payload = build_readiness_payload(
        _manifest(),
        counts={"chapters": 882, "passages": 12_345, "embeddings": 12_345},
        migrations=[
            "control:0001_base",
            "control:0007_release_readiness",
            "data:genereview:0001_chapters",
        ],
        indexes=["genereview_embeddings_bge384_hnsw_cosine"],
        source_digest="sha256:" + "1" * 64,
        query_result_sha256="2" * 64,
        artifact_digest="sha256:" + "3" * 64,
        manifest_digest="sha256:" + "4" * 64,
        checksums_digest="sha256:" + "5" * 64,
        release_tag="corpus-data-2026-08-30-r1",
    )

    assert set(payload) == {
        "release_tag",
        "artifact_digest",
        "manifest_digest",
        "checksums_digest",
        "schema_version",
        "counts",
        "migrations",
        "indexes",
        "source_digest",
        "query_result_sha256",
        "restore_count",
        "restore_mode",
        "operation_order",
        "ready",
        "readiness_marker",
    }
    assert payload["operation_order"][-1] == "readiness-marker"
    assert payload["restore_count"] == 1


def test_readiness_payload_rejects_self_consistent_but_wrong_restore_facts() -> None:
    with pytest.raises(ReadinessError, match="counts"):
        build_readiness_payload(
            _manifest(),
            counts={"chapters": 882, "passages": 12_344, "embeddings": 12_344},
            migrations=[
                "control:0001_base",
                "control:0007_release_readiness",
                "data:genereview:0001_chapters",
            ],
            indexes=["genereview_embeddings_bge384_hnsw_cosine"],
            source_digest="sha256:" + "1" * 64,
            query_result_sha256="2" * 64,
            artifact_digest="sha256:" + "3" * 64,
            manifest_digest="sha256:" + "4" * 64,
            checksums_digest="sha256:" + "5" * 64,
            release_tag="corpus-data-2026-08-30-r1",
        )


def test_readiness_migration_binds_exact_logical_volumes() -> None:
    migration = (
        resources.files("genereview_link.db.migrations.control")
        .joinpath("0007_release_readiness.sql")
        .read_text()
    )
    for volume in ("genereview_pg_data", "genereview_pg_run", "genereview_restore_state"):
        assert volume in migration
