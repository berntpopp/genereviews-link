"""Strict stdlib-only verification of a data-only corpus bundle."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from genereview_link.corpus.bundle_integrity import (
    BundleIntegrityError,
    _canonical_json,
    _load_json,
    _sha256,
    _verify_source,
)
from genereview_link.corpus.computation_validation import validate_computation_provenance
from genereview_link.corpus.pg_client import PG18_IMAGE

MAINTAINER_PREBUILT = "maintainer-prebuilt"


def _computation_run_id(
    *, phase: str, corpus_version: str, expected_row_count: int, provenance: dict[str, object]
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "phase": phase,
                "corpus_version": corpus_version,
                "expected_row_count": expected_row_count,
                "provenance": provenance,
            }
        )
    ).hexdigest()


def verify_data_only_bundle_impl(
    bundle: Path, *, directory_fd: int | None = None
) -> dict[str, object]:
    """Validate a locally built bundle before restore or publication."""
    _verify_source(bundle, directory_fd=directory_fd)
    metadata = _load_json(bundle / "manifest.json", parent_fd=directory_fd)
    from genereview_link.corpus.bundle import BundleManifest

    expected = BundleManifest()
    stable = set(expected.__dataclass_fields__) - {"created_at", "checksums"}
    if set(metadata) != stable | {"checksums"}:
        raise BundleIntegrityError("manifest.json has missing, extra, or volatile fields")
    for name in stable:
        if type(metadata[name]) is not type(getattr(expected, name)):
            raise BundleIntegrityError(f"manifest.json field has invalid type: {name}")
    if (
        metadata["manifest_version"] != "3"
        or metadata["bundle_format"] != "postgresql-custom-data-only"
    ):
        raise BundleIntegrityError("manifest.json is not a v3 data-only bundle")
    _verify_build_provenance(metadata)
    _verify_rights_notice(metadata)
    app_git_sha = metadata.get("app_git_sha")
    if not (
        isinstance(app_git_sha, str)
        and len(app_git_sha) in {40, 64}
        and all(char in "0123456789abcdef" for char in app_git_sha)
    ):
        raise BundleIntegrityError("manifest.json application Git revision is incomplete")
    app_version = metadata.get("app_version")
    if not (
        isinstance(app_version, str)
        and app_version
        and metadata.get("genereview_link_version") == app_version
    ):
        raise BundleIntegrityError("manifest.json application version identity is incomplete")
    source_sha256 = metadata.get("tarball_source_sha256")
    if not (
        isinstance(source_sha256, str)
        and len(source_sha256) == 64
        and all(char in "0123456789abcdef" for char in source_sha256)
    ):
        raise BundleIntegrityError(
            "manifest.json tarball_source_sha256 must be a lowercase SHA-256"
        )
    embedding = metadata.get("embedding")
    if not isinstance(embedding, dict) or set(embedding) != {
        "model_name",
        "dimension",
        "distance_metric",
        "active_table",
        "count",
        "expected_count",
    }:
        raise BundleIntegrityError("manifest.json embedding identity is incomplete")
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
        raise BundleIntegrityError("manifest.json embedding identity has invalid types")
    if (
        embedding["count"] != metadata["passage_count"]
        or embedding["expected_count"] != metadata["passage_count"]
    ):
        raise BundleIntegrityError("manifest.json embedding count does not match passage_count")
    hnsw = metadata.get("hnsw")
    if not (
        isinstance(hnsw, dict)
        and set(hnsw) == {"index_name", "exists"}
        and hnsw.get("index_name") == "genereview_embeddings_bge384_hnsw_cosine"
        and hnsw.get("exists") is True
    ):
        raise BundleIntegrityError("manifest.json lacks the validated HNSW identity")
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
        raise BundleIntegrityError("manifest.json schema migration identity is incomplete")
    from genereview_link.corpus.schema_identity import (
        EXPECTED_CONTROL_MIGRATIONS,
        EXPECTED_DATA_MIGRATIONS,
    )

    if (
        set(migrations["control"]) != EXPECTED_CONTROL_MIGRATIONS
        or set(migrations["data"]) != EXPECTED_DATA_MIGRATIONS
    ):
        raise BundleIntegrityError(
            "manifest.json schema migrations do not match reviewed migrations"
        )
    migration_digests = metadata.get("migration_file_sha256")
    from genereview_link.corpus.bundle import _reviewed_migration_digests

    expected_migration_digests = _reviewed_migration_digests()
    if migration_digests != expected_migration_digests:
        raise BundleIntegrityError("manifest.json migration file digests do not match reviewed SQL")
    postgres = metadata.get("postgres")
    if not (
        isinstance(postgres, dict)
        and set(postgres) == {"major_version", "pgvector_version"}
        and all(isinstance(postgres[name], str) and postgres[name] for name in postgres)
    ):
        raise BundleIntegrityError("manifest.json PostgreSQL identity is incomplete")
    if postgres != {"major_version": "18", "pgvector_version": "0.8.2"}:
        raise BundleIntegrityError(
            "manifest.json PostgreSQL identity does not match reviewed runtime"
        )
    from genereview_link.corpus.source_identity import validate_source_identity

    source_capture = metadata.get("source_capture")
    if not isinstance(source_capture, dict):
        raise BundleIntegrityError("manifest.json retained source/content identity is incomplete")
    try:
        validate_source_identity(
            metadata.get("source"),
            tarball_sha256=source_sha256,
            last_updated=str(metadata.get("tarball_last_updated")),
        )
    except ValueError as error:
        raise BundleIntegrityError(
            "manifest.json upstream source identity is incomplete"
        ) from error
    validation = metadata.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise BundleIntegrityError("manifest.json lacks a passing candidate validation")
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
        raise BundleIntegrityError("manifest.json lacks exact evaluation evidence")
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
        or set(evaluation["results"])
        != {
            "mrr_at_10",
            "section_precision_at_5",
            "queries_run",
            "covered_queries",
            "per_query",
        }
        or type(evaluation["results"]["queries_run"]) is not int
        or evaluation["results"]["queries_run"] != 5
        or evaluation["results"]["covered_queries"] != 5
        or not isinstance(evaluation["results"]["per_query"], list)
        or len(evaluation["results"]["per_query"]) != 5
        or any(
            type(evaluation["results"][name]) not in {int, float}
            or not math.isfinite(evaluation["results"][name])
            or not 0 <= evaluation["results"][name] <= 1
            for name in ("mrr_at_10", "section_precision_at_5")
        )
    ):
        raise BundleIntegrityError("manifest.json evaluation evidence is invalid")
    from genereview_link.corpus.evaluation_contract import (
        EVALUATION_SUITE_SHA256,
        EVALUATION_TOLERANCE,
        MIN_MRR_AT_10,
        MIN_SECTION_PRECISION_AT_5,
    )

    results = evaluation["results"]
    assert isinstance(results, dict)
    per_query = results["per_query"]
    assert isinstance(per_query, list)
    if (
        evaluation["suite_sha256"] != EVALUATION_SUITE_SHA256
        or float(results["mrr_at_10"]) + EVALUATION_TOLERANCE < MIN_MRR_AT_10
        or float(results["section_precision_at_5"]) + EVALUATION_TOLERANCE
        < MIN_SECTION_PRECISION_AT_5
        or any(
            not isinstance(query, dict)
            or set(query)
            != {
                "query_sha256",
                "expected_chapter",
                "expected_section",
                "expected_rank",
                "section_hit_at_5",
                "results_returned",
            }
            or not re.fullmatch(r"[0-9a-f]{64}", str(query.get("query_sha256", "")))
            or not isinstance(query.get("expected_chapter"), str)
            or not isinstance(query.get("expected_section"), str)
            or (
                query.get("expected_rank") is not None
                and (
                    type(query.get("expected_rank")) is not int
                    or not 1 <= int(query["expected_rank"]) <= 10
                )
            )
            or type(query.get("section_hit_at_5")) is not bool
            or type(query.get("results_returned")) is not int
            or not 1 <= int(query["results_returned"]) <= 10
            for query in per_query
        )
    ):
        raise BundleIntegrityError(
            "manifest.json evaluation did not meet the reviewed suite contract"
        )
    if (
        hashlib.sha256(_canonical_json(evaluation["results"])).hexdigest()
        != evaluation["result_sha256"]
    ):
        raise BundleIntegrityError("manifest.json evaluation result digest mismatch")
    computation_identity = metadata.get("computation")
    expected_corpus_identity = {
        "corpus_version": metadata["corpus_version"],
        "source": metadata["source"],
        "chapter_count": metadata["chapter_count"],
        "passage_count": metadata["passage_count"],
        "embedding_count": embedding["count"],
        "embedding_run_id": computation_identity.get("run_id")
        if isinstance(computation_identity, dict)
        else None,
        "content_identity": metadata["content_identity"],
    }
    if evaluation["corpus_identity"] != expected_corpus_identity:
        raise BundleIntegrityError(
            "manifest.json evaluation is not bound to the bundled corpus identity"
        )
    dump_digest, _ = _sha256(bundle / "corpus.dump", parent_fd=directory_fd)
    if evaluation["dump_sha256"] != dump_digest:
        raise BundleIntegrityError("manifest.json evaluation is not bound to corpus.dump")
    computation = metadata.get("computation")
    from genereview_link.retrieval.model_identity import (
        BGE_MODEL_FILES,
        BGE_MODEL_NAME,
        BGE_MODEL_REVISION,
    )

    if not isinstance(computation, dict) or set(computation) != {
        "run_id",
        "app_git_sha",
        "expected_row_count",
        "provenance",
        "ingest_run",
    }:
        raise BundleIntegrityError("manifest.json computation provenance is incomplete")
    provenance = computation.get("provenance")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", str(computation["run_id"]))
        or computation["app_git_sha"] != app_git_sha
        or computation["expected_row_count"] != metadata["passage_count"]
        or not isinstance(provenance, dict)
        or set(provenance)
        != {
            "schema",
            "source",
            "uv_lock_sha256",
            "environment",
            "database",
            "model",
            "determinism",
            "embedding",
        }
        or provenance["schema"] != "genereviews-computation-v2"
        or not re.fullmatch(r"[0-9a-f]{64}", str(provenance["uv_lock_sha256"]))
        or provenance["model"]
        != {
            "name": BGE_MODEL_NAME,
            "revision": BGE_MODEL_REVISION,
            "files": BGE_MODEL_FILES,
        }
        or provenance["embedding"]
        != {
            "model_name": BGE_MODEL_NAME,
            "model_revision": BGE_MODEL_REVISION,
            "table": "genereview_embeddings_bge384",
        }
    ):
        raise BundleIntegrityError("manifest.json model computation identity is invalid")
    ingest_run = computation["ingest_run"]
    if (
        not isinstance(ingest_run, dict)
        or set(ingest_run) != {"run_id", "app_git_sha", "expected_row_count", "provenance"}
        or not re.fullmatch(r"[0-9a-f]{64}", str(ingest_run["run_id"]))
        or not re.fullmatch(r"[0-9a-f]{40}", str(ingest_run["app_git_sha"]))
        or ingest_run["expected_row_count"] != metadata["chapter_count"]
        or not isinstance(ingest_run["provenance"], dict)
        or set(ingest_run["provenance"])
        != {
            "schema",
            "source",
            "source_capture",
            "uv_lock_sha256",
            "environment",
            "database",
            "model",
            "determinism",
            "embedding",
        }
        or ingest_run["provenance"].get("schema") != "genereviews-computation-v2"
        or ingest_run["provenance"].get("source_capture") != metadata["source_capture"]
        or not isinstance(ingest_run["provenance"].get("source"), dict)
        or ingest_run["provenance"].get("source", {}).get("app_git_sha")
        != ingest_run["app_git_sha"]
    ):
        raise BundleIntegrityError("manifest.json ingest computation identity is invalid")
    ingest_provenance = ingest_run["provenance"]
    assert isinstance(ingest_provenance, dict)
    if computation["run_id"] != _computation_run_id(
        phase="embedding",
        corpus_version=str(metadata["corpus_version"]),
        expected_row_count=int(computation["expected_row_count"]),
        provenance=provenance,
    ) or ingest_run["run_id"] != _computation_run_id(
        phase="ingest",
        corpus_version=str(metadata["corpus_version"]),
        expected_row_count=int(ingest_run["expected_row_count"]),
        provenance=ingest_provenance,
    ):
        raise BundleIntegrityError("manifest.json computation run ID is not content-addressed")
    source_provenance = provenance["source"]
    environment = provenance["environment"]
    database = provenance["database"]
    determinism = provenance["determinism"]
    if (
        not isinstance(source_provenance, dict)
        or set(source_provenance) != {"app_git_sha", "builder_identity"}
        or source_provenance["app_git_sha"] != app_git_sha
        or not isinstance(source_provenance["builder_identity"], str)
        or not source_provenance["builder_identity"]
        or not isinstance(environment, dict)
        or set(environment)
        != {
            "installed_distributions",
            "installed_distributions_sha256",
            "uv_version",
            "python",
            "os",
            "kernel",
            "libc",
            "cpu",
            "blas",
            "device",
            "gpu",
            "cuda",
            "cudnn",
            "torch",
            "sentence_transformers",
            "transformers",
            "build_backend",
        }
        or not isinstance(environment["installed_distributions"], list)
        or environment["installed_distributions"] != sorted(environment["installed_distributions"])
        or not all(
            isinstance(value, str) and "==" in value
            for value in environment["installed_distributions"]
        )
        or hashlib.sha256(_canonical_json(environment["installed_distributions"])).hexdigest()
        != environment["installed_distributions_sha256"]
        or any(
            not isinstance(environment[name], str) or not environment[name]
            for name in set(environment) - {"installed_distributions"}
        )
        or environment["device"] not in {"cpu", "cuda"}
        or not isinstance(database, dict)
        or set(database)
        != {
            "client_image",
            "client_major",
            "server_version_num",
            "server_major",
            "pgvector",
        }
        or database["client_major"] != "18"
        or database["server_major"] != "18"
        or database["pgvector"] != "0.8.2"
        or database["client_image"] != PG18_IMAGE
        or not isinstance(determinism, dict)
        or determinism
        != {
            "normalize_embeddings": True,
            "python_seed": 0,
            "numpy_seed": 0,
            "torch_seed": 0,
            "batch_size": determinism.get("batch_size") if isinstance(determinism, dict) else None,
        }
        or type(determinism.get("batch_size")) is not int
        or determinism["batch_size"] <= 0
    ):
        raise BundleIntegrityError("manifest.json runtime computation provenance is invalid")
    try:
        validate_computation_provenance(provenance, app_git_sha=str(app_git_sha))
        validate_computation_provenance(
            ingest_provenance,
            app_git_sha=str(ingest_run["app_git_sha"]),
            source_capture=source_capture,
        )
    except ValueError as error:
        raise BundleIntegrityError(
            "manifest.json runtime computation provenance is invalid"
        ) from error
    for field in (
        "uv_lock_sha256",
        "environment",
        "database",
        "model",
        "determinism",
        "embedding",
    ):
        if ingest_provenance[field] != provenance[field]:
            raise BundleIntegrityError("ingest and embedding runtime provenance do not match")
    content_identity = metadata.get("content_identity")
    if (
        source_capture.get("format") != "genereviews-offline-source-v1"
        or not isinstance(content_identity, dict)
        or content_identity.get("chapter_ids") != source_capture.get("chapter_ids")
        or content_identity.get("source_archive")
        != {
            "members_sha256": source_capture.get("archive", {}).get("members_sha256")
            if isinstance(source_capture.get("archive"), dict)
            else None,
            "expanded_sha256": source_capture.get("archive", {}).get("expanded_sha256")
            if isinstance(source_capture.get("archive"), dict)
            else None,
        }
    ):
        raise BundleIntegrityError("manifest.json retained source/content identity is incomplete")
    from genereview_link.corpus.source_identity import validate_release_id

    try:
        validate_release_id(str(metadata["corpus_release_id"]))
    except ValueError as error:
        raise BundleIntegrityError("manifest.json corpus_release_id is invalid") from error
    updated = metadata.get("tarball_last_updated")
    release_id = str(metadata["corpus_release_id"])
    if (
        not isinstance(updated, str)
        or len(updated) < 10
        or release_id[:10] != updated[:10]
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated[:10])
    ):
        raise BundleIntegrityError(
            "manifest.json corpus_release_id date must match upstream last_updated date"
        )
    checksums = metadata["checksums"]
    if not isinstance(checksums, dict) or set(checksums) != {"corpus.dump"}:
        raise BundleIntegrityError("manifest.json checksums must cover exactly corpus.dump")
    if checksums["corpus.dump"] != dump_digest:
        raise BundleIntegrityError("manifest checksum mismatch for corpus.dump")
    return metadata


def _verify_build_provenance(metadata: dict[str, object]) -> None:
    """The only honest provenance claim this scheme can make.

    The corpus is built on the maintainer's workstation because the embedding pass is
    far too slow for a hosted runner. Nothing here is signed or attested by CI, and the
    published manifest must say so in as many words rather than leaving a reader to
    assume a build provenance that does not exist.
    """
    if metadata.get("build_provenance") != MAINTAINER_PREBUILT:
        raise BundleIntegrityError(
            "manifest.json must declare build_provenance " + MAINTAINER_PREBUILT
        )


def _verify_rights_notice(metadata: dict[str, object]) -> None:
    """The published notice must be the committed one, validated the same way."""
    from genereview_link.corpus.rights_notice import (
        RightsNoticeError,
        load_rights_notice,
        validate_rights_notice,
    )

    published = metadata.get("rights_notice")
    try:
        notice = validate_rights_notice(published)
    except RightsNoticeError as error:
        raise BundleIntegrityError(f"manifest.json rights notice is invalid: {error}") from error
    try:
        committed = load_rights_notice()
    except RightsNoticeError as error:  # pragma: no cover - checkout without data/RIGHTS.json
        raise BundleIntegrityError(f"committed rights notice is unusable: {error}") from error
    if notice.digest != committed.digest:
        raise BundleIntegrityError(
            "manifest.json rights notice does not match the committed data/RIGHTS.json"
        )


__all__ = ["MAINTAINER_PREBUILT", "verify_data_only_bundle_impl"]
