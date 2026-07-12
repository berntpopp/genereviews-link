"""Typer-based CLI for the GeneReview Link unified server."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn

from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog, get_logger

configure_structlog()
logger = get_logger("cli")

app = typer.Typer(
    name="genereview-link",
    help="GeneReview Link Unified Server",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback()
def _main() -> None:
    """GeneReview Link Unified Server."""


class Transport(StrEnum):
    """Transport mode for the server."""

    unified = "unified"
    http = "http"
    stdio = "stdio"


class LogLevel(StrEnum):
    """Supported log levels."""

    debug = "DEBUG"
    info = "INFO"
    warning = "WARNING"
    error = "ERROR"


def build_config(
    transport: Transport = Transport.unified,
    host: str = "127.0.0.1",
    port: int = 8000,
    mcp_path: str = "/mcp",
    disable_docs: bool = False,
    log_level: LogLevel = LogLevel.info,
) -> ServerConfig:
    """Build a ServerConfig from CLI inputs."""
    return ServerConfig(
        transport=transport.value,
        host=host,
        port=port,
        mcp_path=mcp_path,
        enable_docs=not disable_docs,
        log_level=log_level.value,
    )


@app.command()
def serve(
    transport: Annotated[
        Transport, typer.Option("--transport", help="Transport mode")
    ] = Transport.unified,
    host: Annotated[str, typer.Option("--host", help="Host to bind to")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to bind to")] = 8000,
    mcp_path: Annotated[str, typer.Option("--mcp-path", help="MCP endpoint path")] = "/mcp",
    disable_docs: Annotated[
        bool,
        typer.Option("--disable-docs", help="Disable API documentation endpoints"),
    ] = False,
    log_level: Annotated[LogLevel, typer.Option("--log-level", help="Log level")] = LogLevel.info,
    dev: Annotated[bool, typer.Option("--dev", help="Development mode with auto-reload")] = False,
) -> None:
    """Start the GeneReview Link unified server."""
    from genereview_link.server_manager import UnifiedServerManager

    config = build_config(
        transport=transport,
        host=host,
        port=port,
        mcp_path=mcp_path,
        disable_docs=disable_docs,
        log_level=log_level,
    )

    if dev and config.transport != "stdio":
        logger.info("Running in development mode with auto-reload.")
        uvicorn.run(
            "server:app",
            host=config.host,
            port=config.port,
            reload=True,
            log_config=None,
        )
        return

    try:
        manager = UnifiedServerManager()
        asyncio.run(manager.start_server(config))
    except (ValueError, asyncio.CancelledError) as exc:
        logger.error("Server startup failed", error=str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user.")
        sys.exit(0)


db_app = typer.Typer(name="db", help="Database administration commands.")
app.add_typer(db_app)


@db_app.command("migrate")
def db_migrate(
    schema: Annotated[
        str,
        typer.Option("--schema", help="Data schema to apply data migrations into."),
    ] = "genereview",
) -> None:
    """Apply control and data migrations against DATABASE_URL."""
    from genereview_link.db.identifiers import validate_schema_identifier
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

    try:
        validate_schema_identifier(schema)
    except ValueError:
        raise typer.BadParameter(
            "must be a valid PostgreSQL identifier "
            "(letters, digits, underscore; not starting with a digit; <=63 chars)",
            param_hint="--schema",
        ) from None

    async def run() -> None:
        pool = await create_pool()
        try:
            control = await apply_control_migrations(pool)
            data = await apply_data_migrations(pool, schema=schema)
            for v in control:
                typer.echo(f"control: {v}")
            for v in data:
                typer.echo(f"data: {v}")
            if not control and not data:
                typer.echo("nothing to apply (all migrations already applied)")
        finally:
            await pool.close()

    asyncio.run(run())


@db_app.command("reset")
def db_reset(
    confirm: Annotated[bool, typer.Option("--yes", help="Confirm destructive operation.")] = False,
) -> None:
    """DROP genereview/genereview_staging schemas and re-run migrations (dev only)."""
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

    if not confirm:
        typer.echo("Refusing to reset without --yes")
        raise typer.Exit(1)

    async def run() -> None:
        pool = await create_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("drop schema if exists genereview cascade")
                await conn.execute("drop schema if exists genereview_staging cascade")
                rows = await conn.fetch(
                    "select schema_name from information_schema.schemata "
                    "where schema_name like 'genereview_old_%'"
                )
                for row in rows:
                    await conn.execute(f"drop schema {row['schema_name']} cascade")
                # Clear stale data-migration records so the next apply re-creates tables.
                await conn.execute(
                    "delete from public.schema_migrations "
                    "where namespace = 'data' "
                    "and (version like 'genereview:%' or version like 'genereview_staging:%' "
                    "     or version like 'genereview_old_%:%')"
                )
            await apply_control_migrations(pool)
            await apply_data_migrations(pool, schema="genereview")
            typer.echo("reset complete")
        finally:
            await pool.close()

    asyncio.run(run())


@app.command("ingest")
def ingest_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Download + parse only; do not write to DB."),
    ] = False,
) -> None:
    """Run the full ingest pipeline against DATABASE_URL."""
    import asyncio

    from genereview_link.corpus.pipeline import run_full_ingest
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            if dry_run:
                typer.echo("dry-run not yet implemented; aborting")
                raise typer.Exit(2)
            result = await run_full_ingest(pool)
            typer.echo(
                f"ingested {result.chapter_count} chapters / "
                f"{result.passage_count} passages "
                f"as corpus_version={result.corpus_version}"
            )
        finally:
            await pool.close()

    asyncio.run(run())


@app.command("embed")
def embed_cmd(
    schema: Annotated[str, typer.Option("--schema")] = "genereview",
    fake: Annotated[
        bool, typer.Option("--fake", help="Use deterministic FakeEmbeddingProvider (testing).")
    ] = False,
) -> None:
    """Backfill BGE embeddings for missing passages and build HNSW index."""
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
    from genereview_link.retrieval.embeddings import (
        FakeEmbeddingProvider,
        SentenceTransformerEmbeddingProvider,
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            provider = (
                FakeEmbeddingProvider(dim=384) if fake else SentenceTransformerEmbeddingProvider()
            )
            count = await backfill_embeddings(pool, provider, schema=schema)
            typer.echo(f"embedded {count} passages")
            await build_hnsw_index(pool, schema=schema)
            typer.echo("HNSW index built")
        finally:
            await pool.close()

    asyncio.run(run())


bundle_app = typer.Typer(name="bundle", help="Build and verify release bundles.")
app.add_typer(bundle_app)


def _run_gh(args: list[str]) -> None:
    try:
        subprocess.run(["gh", *args], check=True)  # noqa: S603, S607
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI 'gh' is required for upload") from exc


def _build_bundle(
    *,
    output: Path | None,
    release_id: str | None,
    skip_validation: bool,
) -> Path:
    """Build a corpus bundle from DATABASE_URL and return the tarball path."""
    from datetime import UTC, datetime

    from genereview_link.config import settings
    from genereview_link.corpus.bundle import (
        BundleManifest,
        pg_dump_to,
        write_bundle,
    )
    from genereview_link.corpus.bundle_metadata import asset_name_for_release
    from genereview_link.corpus.bundle_validation import validate_database_ready
    from genereview_link.db.pool import create_pool

    if release_id and output is None:
        output = Path(
            asset_name_for_release(
                release_id,
                model_slug="bge-small-en-v1.5",
                postgres_major="pg18",
                pgvector_version="pgv0.8.2",
            )
        )
    output = output or Path("genereview-corpus.tar.gz")

    async def run() -> Path:
        pool = await create_pool()
        try:
            row = await pool.fetchrow(
                "select version, chapter_count from public.genereview_corpus_version where is_active"
            )
            if not row:
                typer.echo("no active corpus version; aborting")
                raise typer.Exit(1)

            validation_manifest: dict[str, Any] = {
                "status": "not_run",
                "smoke_queries": [],
            }
            if not skip_validation:
                validation = await validate_database_ready(pool)
                validation_manifest = validation.as_manifest()
                if not validation.ok:
                    for error in validation.errors:
                        typer.echo(f"error: {error}", err=True)
                    raise typer.Exit(1)

            passage_count = await pool.fetchval(
                'select count(*) from "genereview".genereview_passages'
            )
            embedding_count = await pool.fetchval(
                """
                select count(*)
                  from "genereview".genereview_embeddings_bge384
                 where model_name = 'BAAI/bge-small-en-v1.5'
                """
            )
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                pg_dump_to(td_path / "corpus.dump", database_url=settings.DATABASE_URL)
                sidedata = td_path / "sidedata"
                sidedata.mkdir()
                from genereview_link.corpus.pipeline import _download_sidedata

                await _download_sidedata(sidedata)
                m = BundleManifest(
                    corpus_release_id=release_id or "",
                    corpus_version=row["version"],
                    chapter_count=row["chapter_count"] or 0,
                    passage_count=int(passage_count or 0),
                    embedding={
                        "model_name": "BAAI/bge-small-en-v1.5",
                        "dimension": 384,
                        "distance_metric": "cosine",
                        "active_table": "genereview_embeddings_bge384",
                        "count": int(embedding_count or 0),
                        "expected_count": int(passage_count or 0),
                    },
                    created_at=datetime.now(UTC).isoformat(),
                    created_by="cli",
                    validation=validation_manifest,
                )
                write_bundle(work_dir=td_path, output=output, manifest=m, sidedata_dir=sidedata)
                return output
        finally:
            await pool.close()

    return asyncio.run(run())


@bundle_app.command("validate")
def bundle_validate() -> None:
    """Validate that DATABASE_URL is ready for bundle publishing."""
    from genereview_link.corpus.bundle_validation import validate_database_ready
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            result = await validate_database_ready(pool)
            for warning in result.warnings:
                typer.echo(f"warning: {warning}")
            if not result.ok:
                for error in result.errors:
                    typer.echo(f"error: {error}", err=True)
                raise typer.Exit(1)
            typer.echo("bundle validation passed")
        finally:
            await pool.close()

    asyncio.run(run())


@bundle_app.command("build")
def bundle_build(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    release_id: Annotated[str | None, typer.Option("--release-id")] = None,
    skip_validation: Annotated[
        bool,
        typer.Option("--skip-validation", help="Build without publish-readiness validation."),
    ] = False,
) -> None:
    """Build a release bundle from the current DATABASE_URL."""
    built = _build_bundle(output=output, release_id=release_id, skip_validation=skip_validation)
    typer.echo(f"wrote {built} (+ {built}.sha256)")


@bundle_app.command("publish-local")
def bundle_publish_local(
    release_id: Annotated[str, typer.Option("--release-id")],
    device: Annotated[str, typer.Option("--device")] = "cuda",
    repo: Annotated[str, typer.Option("--repo")] = "berntpopp/genereviews-link",
    draft: Annotated[bool, typer.Option("--draft/--no-draft")] = True,
    upload: Annotated[bool, typer.Option("--upload/--no-upload")] = True,
) -> None:
    """Build a local CUDA corpus bundle and optionally upload it to GitHub Releases."""
    from genereview_link.corpus.bundle_metadata import asset_name_for_release, validate_release_id
    from genereview_link.corpus.pipeline import run_full_ingest
    from genereview_link.db.migrate import apply_control_migrations
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
    from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    validate_release_id(release_id)
    if device.startswith("cuda"):
        import torch

        if not torch.cuda.is_available():
            typer.echo("CUDA requested but torch.cuda.is_available() is false", err=True)
            raise typer.Exit(1)

    output = Path(
        asset_name_for_release(
            release_id,
            model_slug="bge-small-en-v1.5",
            postgres_major="pg18",
            pgvector_version="pgv0.8.2",
        )
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            await apply_control_migrations(pool)
            await run_full_ingest(pool)
            os.environ["INGEST_EMBED_DEVICE"] = device
            provider = SentenceTransformerEmbeddingProvider(device=device)
            embedded = await backfill_embeddings(pool, provider)
            typer.echo(f"embedded {embedded} passages")
            await build_hnsw_index(pool)
        finally:
            await pool.close()

    asyncio.run(run())
    built = _build_bundle(output=output, release_id=release_id, skip_validation=False)

    if upload:
        tag = f"corpus-{release_id}"
        release_args = ["release", "create", tag, str(built), f"{built}.sha256", "--repo", repo]
        if draft:
            release_args.append("--draft")
        release_args.extend(
            ["--title", tag, "--notes", f"Precomputed GeneReviews corpus bundle {release_id}"]
        )
        _run_gh(release_args)
        typer.echo(f"uploaded {built} to {repo} release {tag}")
    else:
        typer.echo(f"built {built}; upload skipped")


if __name__ == "__main__":
    app()
