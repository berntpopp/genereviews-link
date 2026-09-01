"""Build one evaluated data-only bundle from a locked database snapshot."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import typer


def build_bundle(
    *,
    output: Path | None,
    release_id: str | None,
    skip_validation: bool,
    evaluation_file: Path | None = None,
) -> Path:
    """Build a local data-only corpus directory from DATABASE_URL."""

    from genereview_link.config import settings
    from genereview_link.corpus.bundle import (
        BundleManifest,
        pg_dump_to,
        write_data_only_bundle,
    )
    from genereview_link.corpus.bundle_metadata import (
        collect_database_facts_from_connection,
        resolve_app_git_sha,
    )
    from genereview_link.corpus.bundle_validation import validate_database_ready_from_connection
    from genereview_link.corpus.computation_provenance import collect_computation_provenance
    from genereview_link.corpus.evaluation import (
        build_evaluation_evidence,
        evaluate_connection,
    )
    from genereview_link.db.locks import CORPUS_WRITE_LOCK_KEY
    from genereview_link.db.pool import create_pool

    if release_id and output is None:
        output = Path(f"genereview-corpus-data-{release_id}")
    output = output or Path("genereview-corpus-data")

    async def run() -> Path:
        pool = await create_pool()
        try:
            # Fast fail before opening the fenced export transaction.  The complete
            # identity is collected again inside that transaction below.
            if (
                await pool.fetchrow(
                    "select 1 from public.genereview_corpus_version "
                    "where is_active and ingest_status = 'completed'"
                )
                is None
            ):
                typer.echo("no active corpus version; aborting")
                raise typer.Exit(1)
            from genereview_link import __version__

            app_git_sha = resolve_app_git_sha()
            computation = collect_computation_provenance(app_git_sha=app_git_sha)

            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                async with pool.acquire() as connection:  # noqa: SIM117 - export must stay in transaction
                    async with connection.transaction(isolation="repeatable_read", readonly=True):
                        await connection.execute(
                            "select pg_advisory_xact_lock($1)", CORPUS_WRITE_LOCK_KEY
                        )
                        facts = await collect_database_facts_from_connection(connection)
                        if facts is None:
                            typer.echo("no active corpus version; aborting")
                            raise typer.Exit(1)
                        validation_manifest: dict[str, Any] = {
                            "status": "not_run",
                            "smoke_queries": [],
                        }
                        if not skip_validation:
                            validation = await validate_database_ready_from_connection(connection)
                            validation_manifest = validation.as_manifest()
                            if not validation.ok:
                                for error in validation.errors:
                                    typer.echo(f"error: {error}", err=True)
                                raise typer.Exit(1)
                        snapshot = await connection.fetchval("select pg_export_snapshot()")
                        snapshot_id = str(snapshot)
                        metrics = await evaluate_connection(connection)
                        pg_dump_to(
                            td_path / "corpus.dump",
                            database_url=settings.DATABASE_URL,
                            snapshot=snapshot_id,
                        )
                        from genereview_link.corpus.bundle import sha256_file

                        evaluation = build_evaluation_evidence(
                            metrics,
                            corpus_identity={
                                "corpus_version": facts.corpus_version,
                                "source": facts.source,
                                "chapter_count": facts.chapter_count,
                                "passage_count": facts.passage_count,
                                "embedding_count": facts.embedding_count,
                            },
                            export_snapshot=snapshot_id,
                            dump_sha256=sha256_file(td_path / "corpus.dump"),
                        )

                if release_id:
                    from genereview_link.corpus.bundle_metadata import validate_release_id

                    validate_release_id(release_id)
                    source_date = facts.tarball_last_updated[:10]
                    if release_id[:10] != source_date:
                        raise typer.BadParameter(
                            "must use the normalized upstream source last_updated date",
                            param_hint="--release-id",
                        )

                if evaluation_file is not None:
                    try:
                        supplied_evaluation = json.loads(evaluation_file.read_text())
                    except (OSError, json.JSONDecodeError) as error:
                        raise typer.BadParameter(
                            "evaluation file must be readable JSON", param_hint="--evaluation-file"
                        ) from error
                    if not isinstance(supplied_evaluation, dict):
                        raise typer.BadParameter(
                            "evaluation file must contain one JSON object",
                            param_hint="--evaluation-file",
                        )
                    if supplied_evaluation != evaluation:
                        raise typer.BadParameter(
                            "evaluation file is not the fresh locked snapshot evidence",
                            param_hint="--evaluation-file",
                        )
                m = BundleManifest(
                    corpus_release_id=release_id or "",
                    corpus_version=facts.corpus_version,
                    tarball_source_sha256=facts.tarball_source_sha256,
                    tarball_last_updated=facts.tarball_last_updated,
                    chapter_count=facts.chapter_count,
                    passage_count=facts.passage_count,
                    embedding={
                        "model_name": "BAAI/bge-small-en-v1.5",
                        "dimension": 384,
                        "distance_metric": "cosine",
                        "active_table": "genereview_embeddings_bge384",
                        "count": facts.embedding_count,
                        "expected_count": facts.passage_count,
                    },
                    postgres=facts.postgres,
                    schema_migrations=facts.schema_migrations,
                    app_git_sha=app_git_sha,
                    app_version=__version__,
                    genereview_link_version=__version__,
                    hnsw={
                        "index_name": "genereview_embeddings_bge384_hnsw_cosine",
                        "exists": facts.hnsw_exists,
                    },
                    source=facts.source,
                    created_by="ci" if os.getenv("GITHUB_ACTIONS") == "true" else "cli",
                    validation=validation_manifest,
                    evaluation=evaluation,
                    computation=computation,
                )
                write_data_only_bundle(work_dir=td_path, output=output, manifest=m)
                return output
        finally:
            await pool.close()

    return asyncio.run(run())


__all__ = ["build_bundle"]
