"""Strict stdlib-only verification of a data-only corpus bundle."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from genereview_link.corpus.handoff import (
    HandoffError,
    _canonical_json,
    _load_json,
    _sha256,
    _verify_source,
)


def verify_data_only_bundle_impl(
    bundle: Path, *, allow_extra: bool = False, directory_fd: int | None = None
) -> dict[str, object]:
    """Validate a fresh local bundle before restore or sealing."""
    _verify_source(bundle, allow_extra=allow_extra, directory_fd=directory_fd)
    metadata = _load_json(bundle / "manifest.json", parent_fd=directory_fd)
    from genereview_link.corpus.bundle import BundleManifest

    expected = BundleManifest()
    stable = set(expected.__dataclass_fields__) - {"created_at", "checksums"}
    if set(metadata) != stable | {"checksums"}:
        raise HandoffError("manifest.json has missing, extra, or volatile fields")
    for name in stable:
        if type(metadata[name]) is not type(getattr(expected, name)):
            raise HandoffError(f"manifest.json field has invalid type: {name}")
    if (
        metadata["manifest_version"] != "3"
        or metadata["bundle_format"] != "postgresql-custom-data-only"
    ):
        raise HandoffError("manifest.json is not a v3 data-only bundle")
    app_git_sha = metadata.get("app_git_sha")
    if not (
        isinstance(app_git_sha, str)
        and len(app_git_sha) in {40, 64}
        and all(char in "0123456789abcdef" for char in app_git_sha)
    ):
        raise HandoffError("manifest.json application Git revision is incomplete")
    app_version = metadata.get("app_version")
    if not (
        isinstance(app_version, str)
        and app_version
        and metadata.get("genereview_link_version") == app_version
    ):
        raise HandoffError("manifest.json application version identity is incomplete")
    source_sha256 = metadata.get("tarball_source_sha256")
    if not (
        isinstance(source_sha256, str)
        and len(source_sha256) == 64
        and all(char in "0123456789abcdef" for char in source_sha256)
    ):
        raise HandoffError("manifest.json tarball_source_sha256 must be a lowercase SHA-256")
    embedding = metadata.get("embedding")
    if not isinstance(embedding, dict) or set(embedding) != {
        "model_name",
        "dimension",
        "distance_metric",
        "active_table",
        "count",
        "expected_count",
    }:
        raise HandoffError("manifest.json embedding identity is incomplete")
    if any(
        type(embedding[name]) is not expected_type
        for name, expected_type in {
            "model_name": str,
            "dimension": int,
            "distance_metric": str,
            "active_table": str,
            "count": int,
            "expected_count": int,
        }.items()
    ):
        raise HandoffError("manifest.json embedding identity has invalid types")
    if (
        embedding["count"] != metadata["passage_count"]
        or embedding["expected_count"] != metadata["passage_count"]
    ):
        raise HandoffError("manifest.json embedding count does not match passage_count")
    hnsw = metadata.get("hnsw")
    if not (
        isinstance(hnsw, dict)
        and set(hnsw) == {"index_name", "exists"}
        and hnsw.get("index_name") == "genereview_embeddings_bge384_hnsw_cosine"
        and hnsw.get("exists") is True
    ):
        raise HandoffError("manifest.json lacks the validated HNSW identity")
    migrations = metadata.get("schema_migrations")
    if not (
        isinstance(migrations, dict)
        and set(migrations) == {"control", "data"}
        and all(
            isinstance(values, list)
            and values
            and all(isinstance(value, str) and value for value in values)
            and len(values) == len(set(values))
            for values in migrations.values()
        )
    ):
        raise HandoffError("manifest.json schema migration identity is incomplete")
    from genereview_link.corpus.schema_identity import (
        EXPECTED_CONTROL_MIGRATIONS,
        EXPECTED_DATA_MIGRATIONS,
    )

    if (
        set(migrations["control"]) != EXPECTED_CONTROL_MIGRATIONS
        or set(migrations["data"]) != EXPECTED_DATA_MIGRATIONS
    ):
        raise HandoffError("manifest.json schema migrations do not match reviewed migrations")
    migration_digests = metadata.get("migration_file_sha256")
    from genereview_link.corpus.bundle import _reviewed_migration_digests

    expected_migration_digests = _reviewed_migration_digests()
    if migration_digests != expected_migration_digests:
        raise HandoffError("manifest.json migration file digests do not match reviewed SQL")
    postgres = metadata.get("postgres")
    if not (
        isinstance(postgres, dict)
        and set(postgres) == {"major_version", "pgvector_version"}
        and all(isinstance(postgres[name], str) and postgres[name] for name in postgres)
    ):
        raise HandoffError("manifest.json PostgreSQL identity is incomplete")
    if postgres != {"major_version": "18", "pgvector_version": "0.8.2"}:
        raise HandoffError("manifest.json PostgreSQL identity does not match reviewed runtime")
    from genereview_link.corpus.source_identity import validate_source_identity

    try:
        validate_source_identity(
            metadata.get("source"),
            tarball_sha256=source_sha256,
            last_updated=str(metadata.get("tarball_last_updated")),
        )
    except ValueError as error:
        raise HandoffError("manifest.json upstream source identity is incomplete") from error
    validation = metadata.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise HandoffError("manifest.json lacks a passing candidate validation")
    evaluation = metadata.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != {
        "status",
        "suite",
        "suite_sha256",
        "model_name",
        "corpus_identity",
        "export_snapshot",
        "dump_sha256",
        "results",
        "result_sha256",
    }:
        raise HandoffError("manifest.json lacks exact evaluation evidence")
    if (
        evaluation["status"] != "passed"
        or evaluation["suite"] != "tests/eval/genereviews_queries.jsonl"
        or evaluation["model_name"] != "BAAI/bge-small-en-v1.5"
        or not isinstance(evaluation["suite_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", evaluation["suite_sha256"])
        or not isinstance(evaluation["result_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", evaluation["result_sha256"])
        or not isinstance(evaluation["export_snapshot"], str)
        or not evaluation["export_snapshot"]
        or not re.fullmatch(r"[0-9a-f]{64}", str(evaluation["dump_sha256"]))
        or not isinstance(evaluation["results"], dict)
        or set(evaluation["results"]) != {"mrr_at_10", "section_precision_at_5", "queries_run"}
        or type(evaluation["results"]["queries_run"]) is not int
        or evaluation["results"]["queries_run"] <= 0
        or any(
            type(evaluation["results"][name]) not in {int, float}
            or not math.isfinite(evaluation["results"][name])
            or not 0 <= evaluation["results"][name] <= 1
            for name in ("mrr_at_10", "section_precision_at_5")
        )
    ):
        raise HandoffError("manifest.json evaluation evidence is invalid")
    if (
        hashlib.sha256(_canonical_json(evaluation["results"])).hexdigest()
        != evaluation["result_sha256"]
    ):
        raise HandoffError("manifest.json evaluation result digest mismatch")
    expected_corpus_identity = {
        "corpus_version": metadata["corpus_version"],
        "source": metadata["source"],
        "chapter_count": metadata["chapter_count"],
        "passage_count": metadata["passage_count"],
        "embedding_count": embedding["count"],
    }
    if evaluation["corpus_identity"] != expected_corpus_identity:
        raise HandoffError("manifest.json evaluation is not bound to the bundled corpus identity")
    dump_digest, _ = _sha256(bundle / "corpus.dump", parent_fd=directory_fd)
    if evaluation["dump_sha256"] != dump_digest:
        raise HandoffError("manifest.json evaluation is not bound to corpus.dump")
    computation = metadata.get("computation")
    from genereview_link.retrieval.model_identity import (
        BGE_MODEL_FILE,
        BGE_MODEL_FILE_SHA256,
        BGE_MODEL_NAME,
        BGE_MODEL_REVISION,
    )

    if not isinstance(computation, dict) or set(computation) != {
        "uv_lock_sha256",
        "model",
        "runtime",
        "determinism",
        "builder",
        "embedding",
    }:
        raise HandoffError("manifest.json computation provenance is incomplete")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", str(computation["uv_lock_sha256"]))
        or computation["model"]
        != {
            "name": BGE_MODEL_NAME,
            "revision": BGE_MODEL_REVISION,
            "files": {BGE_MODEL_FILE: BGE_MODEL_FILE_SHA256},
        }
        or computation["embedding"]
        != {
            "model_name": BGE_MODEL_NAME,
            "model_revision": BGE_MODEL_REVISION,
            "table": "genereview_embeddings_bge384",
        }
    ):
        raise HandoffError("manifest.json model computation identity is invalid")
    runtime = computation["runtime"]
    determinism = computation["determinism"]
    builder = computation["builder"]
    if (
        not isinstance(runtime, dict)
        or set(runtime) != {"python", "torch", "sentence_transformers", "transformers", "device"}
        or not all(isinstance(value, str) and value for value in runtime.values())
        or runtime["device"] not in {"cpu", "cuda"}
        or determinism
        != {
            "normalize_embeddings": True,
            "python_seed": 0,
            "numpy_seed": 0,
            "torch_seed": 0,
            "batch_size": determinism.get("batch_size") if isinstance(determinism, dict) else None,
        }
        or not isinstance(determinism, dict)
        or type(determinism.get("batch_size")) is not int
        or determinism["batch_size"] <= 0
        or not isinstance(builder, dict)
        or set(builder) != {"source_sha", "identity"}
        or builder["source_sha"] != app_git_sha
        or not isinstance(builder["identity"], str)
        or not builder["identity"]
    ):
        raise HandoffError("manifest.json runtime computation provenance is invalid")
    from genereview_link.corpus.source_identity import validate_release_id

    try:
        validate_release_id(str(metadata["corpus_release_id"]))
    except ValueError as error:
        raise HandoffError("manifest.json corpus_release_id is invalid") from error
    updated = metadata.get("tarball_last_updated")
    release_id = str(metadata["corpus_release_id"])
    if (
        not isinstance(updated, str)
        or len(updated) < 10
        or release_id[:10] != updated[:10]
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated[:10])
    ):
        raise HandoffError(
            "manifest.json corpus_release_id date must match upstream last_updated date"
        )
    checksums = metadata["checksums"]
    if not isinstance(checksums, dict) or set(checksums) != {"corpus.dump"}:
        raise HandoffError("manifest.json checksums must cover exactly corpus.dump")
    if checksums["corpus.dump"] != dump_digest:
        raise HandoffError("manifest checksum mismatch for corpus.dump")
    return metadata


__all__ = ["verify_data_only_bundle_impl"]
