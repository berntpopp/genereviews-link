"""Last-written runtime proof for one fully restored GeneReviews release."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from genereview_link.corpus.computation_runs import load_active_computation
from genereview_link.corpus.evaluation import canonical_json, evaluate_connection
from genereview_link.corpus.semantic_identity import collect_content_identity
from genereview_link.db.locks import CORPUS_WRITE_LOCK_KEY

LOGICAL_VOLUMES = ("genereview_pg_data", "genereview_pg_run", "genereview_restore_state")
OPERATION_ORDER = (
    "migrations",
    "data-only-restore",
    "counts",
    "hnsw",
    "source-digest",
    "semantic-query",
    "readiness-marker",
)
READINESS_KEYS = frozenset(
    {
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
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_TAG = re.compile(r"^corpus-data-20[0-9]{2}-[0-9]{2}-[0-9]{2}-r[1-9][0-9]*$")


class ReadinessError(RuntimeError):
    """The restored database does not prove the exact release identity."""


def assert_runtime_manifest_identity(
    manifest: Mapping[str, object],
    *,
    content_identity: Mapping[str, object],
    computation: Mapping[str, object],
) -> None:
    """Require the restored logical content and computation chain to equal the seal."""
    if manifest.get("content_identity") != dict(content_identity):
        raise ReadinessError("restored content identity does not match the release manifest")
    if manifest.get("computation") != dict(computation):
        raise ReadinessError("restored computation identity does not match the release manifest")


def configured_direct_release(
    *,
    release_tag: str,
    artifact_digest: str,
    manifest_digest: str,
    checksums_digest: str,
) -> dict[str, str]:
    """Normalize the complete independently reviewed direct-release identity."""
    if not _RELEASE_TAG.fullmatch(release_tag):
        raise ReadinessError("configured direct release tag is invalid")
    result = {"release_tag": release_tag}
    for name, value in (
        ("artifact_digest", artifact_digest),
        ("manifest_digest", manifest_digest),
        ("checksums_digest", checksums_digest),
    ):
        normalized = value.removeprefix("sha256:").lower()
        if not _SHA256.fullmatch(normalized):
            raise ReadinessError(f"configured direct release {name} is invalid")
        result[name] = f"sha256:{normalized}"
    return result


def _manifest_migrations(manifest: Mapping[str, object]) -> list[str]:
    value = manifest.get("schema_migrations")
    if not isinstance(value, Mapping):
        raise ReadinessError("manifest migration identity is incomplete")
    control, data = value.get("control"), value.get("data")
    if not isinstance(control, list) or not isinstance(data, list):
        raise ReadinessError("manifest migration identity is incomplete")
    return sorted([f"control:{item}" for item in control] + [f"data:{item}" for item in data])


def build_readiness_payload(
    manifest: Mapping[str, object],
    *,
    counts: Mapping[str, int],
    migrations: list[str],
    indexes: list[str],
    source_digest: str,
    query_result_sha256: str,
    artifact_digest: str,
    manifest_digest: str,
    checksums_digest: str,
    release_tag: str,
) -> dict[str, object]:
    """Validate observed facts against the release manifest and build the exact probe JSON."""
    embedding = manifest.get("embedding")
    hnsw = manifest.get("hnsw")
    evaluation = manifest.get("evaluation")
    expected_counts = {
        "chapters": manifest.get("chapter_count"),
        "passages": manifest.get("passage_count"),
        "embeddings": embedding.get("count") if isinstance(embedding, Mapping) else None,
    }
    if dict(counts) != expected_counts or any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in counts.values()
    ):
        raise ReadinessError("restored counts do not match the release manifest")
    if sorted(migrations) != _manifest_migrations(manifest):
        raise ReadinessError("applied migrations do not match the release manifest")
    expected_index = hnsw.get("index_name") if isinstance(hnsw, Mapping) else None
    if hnsw is None or not isinstance(expected_index, str) or indexes != [expected_index]:
        raise ReadinessError("HNSW indexes do not match the release manifest")
    expected_source = manifest.get("tarball_source_sha256")
    if source_digest != f"sha256:{expected_source}" or not _SHA256.fullmatch(str(expected_source)):
        raise ReadinessError("source digest does not match the release manifest")
    expected_query = evaluation.get("result_sha256") if isinstance(evaluation, Mapping) else None
    if query_result_sha256 != expected_query or not _SHA256.fullmatch(query_result_sha256):
        raise ReadinessError("semantic query digest does not match the release manifest")
    checksums = manifest.get("checksums")
    expected_artifact = checksums.get("corpus.dump") if isinstance(checksums, Mapping) else None
    if (
        not isinstance(expected_artifact, str)
        or not _SHA256.fullmatch(expected_artifact)
        or artifact_digest != f"sha256:{expected_artifact}"
    ):
        raise ReadinessError("artifact digest does not match manifest corpus.dump")
    release_id = manifest.get("corpus_release_id")
    configured = configured_direct_release(
        release_tag=release_tag,
        artifact_digest=artifact_digest,
        manifest_digest=manifest_digest,
        checksums_digest=checksums_digest,
    )
    if (
        not isinstance(release_id, str)
        or release_tag != f"corpus-data-{release_id}"
        or artifact_digest != configured["artifact_digest"]
    ):
        raise ReadinessError("release identity is incomplete")
    return {
        **configured,
        "schema_version": manifest.get("manifest_version"),
        "counts": dict(counts),
        "migrations": sorted(migrations),
        "indexes": indexes,
        "source_digest": source_digest,
        "query_result_sha256": query_result_sha256,
        "restore_count": 1,
        "restore_mode": "data-only",
        "operation_order": list(OPERATION_ORDER),
        "ready": True,
        "readiness_marker": "verified-v1",
    }


async def write_release_readiness(
    pool: Any,
    manifest: Mapping[str, object],
    *,
    artifact_digest: str,
    manifest_digest: str,
    checksums_digest: str,
    release_tag: str,
) -> dict[str, object]:
    """Recompute semantics and insert the immutable marker as the final restore write."""
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY)
        existing = await connection.fetchval(
            "select 1 from public.genereview_release_readiness where readiness_key"
        )
        if existing:
            raise ReadinessError("release readiness was already written")
        active = await connection.fetchrow(
            "select version, tarball_sha256 from public.genereview_corpus_version where is_active"
        )
        if active is None or active["version"] != manifest.get("corpus_version"):
            raise ReadinessError("active corpus does not match the release manifest")
        counts_row = await connection.fetchrow(
            "select (select count(*) from genereview.genereview_chapters) as chapters, "
            "(select count(*) from genereview.genereview_passages) as passages, "
            "(select count(*) from genereview.genereview_embeddings_bge384) as embeddings"
        )
        if counts_row is None:
            raise ReadinessError("restored counts are unavailable")
        rows = await connection.fetch(
            "select namespace, version from public.schema_migrations order by namespace, version"
        )
        migrations = [f"{row['namespace']}:{row['version']}" for row in rows]
        index_name = "genereview_embeddings_bge384_hnsw_cosine"
        index_exists = await connection.fetchval(
            "select to_regclass($1) is not null", f"genereview.{index_name}"
        )
        indexes = [index_name] if index_exists else []
        try:
            content_identity = await collect_content_identity(connection)
            computation = await load_active_computation(connection)
        except ValueError as error:
            raise ReadinessError("restored runtime identity is incomplete") from error
        assert_runtime_manifest_identity(
            manifest,
            content_identity=content_identity,
            computation=computation,
        )
        metrics = await evaluate_connection(connection)
        query_digest = hashlib.sha256(canonical_json(metrics)).hexdigest()
        payload = build_readiness_payload(
            manifest,
            counts={name: int(counts_row[name]) for name in ("chapters", "passages", "embeddings")},
            migrations=migrations,
            indexes=indexes,
            source_digest=f"sha256:{active['tarball_sha256']}",
            query_result_sha256=query_digest,
            artifact_digest=artifact_digest,
            manifest_digest=manifest_digest,
            checksums_digest=checksums_digest,
            release_tag=release_tag,
        )
        await connection.execute(
            "insert into public.genereview_release_readiness "
            "(readiness_key, release_tag, is_active, ready, readiness_marker, logical_volumes, readiness) "
            "values (true, $1, true, true, 'verified-v1', $2::text[], $3::jsonb)",
            payload["release_tag"],
            list(LOGICAL_VOLUMES),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
    return payload


async def require_release_readiness(
    pool: Any,
    *,
    release_tag: str,
    artifact_digest: str,
    manifest_digest: str,
    checksums_digest: str,
) -> dict[str, object]:
    configured = configured_direct_release(
        release_tag=release_tag,
        artifact_digest=artifact_digest,
        manifest_digest=manifest_digest,
        checksums_digest=checksums_digest,
    )
    value = await pool.fetchval(
        "select readiness::text from public.genereview_release_readiness "
        "where readiness_key and is_active and ready and readiness_marker = 'verified-v1'"
    )
    if not isinstance(value, str):
        raise ReadinessError("active corpus has no verified release readiness")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ReadinessError("release readiness is invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) != READINESS_KEYS:
        raise ReadinessError("release readiness shape is invalid")
    if (
        payload.get("ready") is not True
        or payload.get("readiness_marker") != "verified-v1"
        or payload.get("restore_count") != 1
        or payload.get("restore_mode") != "data-only"
        or payload.get("operation_order") != list(OPERATION_ORDER)
    ):
        raise ReadinessError("release readiness facts are invalid")
    if any(payload[name] != value for name, value in configured.items()):
        raise ReadinessError("release readiness does not match the configured direct release")
    return payload


__all__ = [
    "LOGICAL_VOLUMES",
    "ReadinessError",
    "assert_runtime_manifest_identity",
    "build_readiness_payload",
    "configured_direct_release",
    "require_release_readiness",
    "write_release_readiness",
]
