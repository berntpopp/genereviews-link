"""Typer-based CLI for the GeneReview Link unified server."""

from __future__ import annotations

import asyncio
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog, get_logger

configure_structlog()
logger = get_logger("cli")


def _build_bundle(
    *,
    output: Path | None,
    release_id: str | None,
    skip_validation: bool,
    evaluation_file: Path | None = None,
) -> Path:
    """Load offline-only corpus tooling only when its CLI command is invoked."""
    from genereview_link.corpus.bundle_builder import build_bundle

    return build_bundle(
        output=output,
        release_id=release_id,
        skip_validation=skip_validation,
        evaluation_file=evaluation_file,
    )


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
    from genereview_link.db.identifiers import quote_pg_identifier
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
                    # Fail closed: validate the catalog-sourced schema name first.
                    quoted = quote_pg_identifier(row["schema_name"])
                    await conn.execute(f"drop schema {quoted} cascade")
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
    archive: Annotated[Path | None, typer.Option("--archive")] = None,
    side_data_dir: Annotated[Path | None, typer.Option("--side-data-dir")] = None,
    source_metadata: Annotated[Path | None, typer.Option("--source-metadata")] = None,
    prior_manifest: Annotated[Path | None, typer.Option("--prior-manifest")] = None,
    prior_seal_manifest: Annotated[Path | None, typer.Option("--prior-seal-manifest")] = None,
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
            result = await run_full_ingest(
                pool,
                archive=archive,
                side_data_dir=side_data_dir,
                source_metadata=source_metadata,
                prior_manifest=prior_manifest,
                prior_seal_manifest=prior_seal_manifest,
            )
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
    index_only: Annotated[
        bool, typer.Option("--index-only", help="Rebuild HNSW without inserting embeddings.")
    ] = False,
) -> None:
    """Backfill BGE embeddings for missing passages and build HNSW index."""
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.orchestrator import (
        backfill_embeddings,
        build_hnsw_index,
        rebuild_hnsw_index,
    )
    from genereview_link.retrieval.embeddings import (
        FakeEmbeddingProvider,
        SentenceTransformerEmbeddingProvider,
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            if index_only:
                await rebuild_hnsw_index(pool, schema=schema)
                typer.echo("HNSW index rebuilt")
                return
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
    evaluation_file: Annotated[Path | None, typer.Option("--evaluation-file")] = None,
    skip_validation: Annotated[
        bool,
        typer.Option("--skip-validation", help="Build without publish-readiness validation."),
    ] = False,
) -> None:
    """Build a release bundle from the current DATABASE_URL."""
    built = _build_bundle(
        output=output,
        release_id=release_id,
        skip_validation=skip_validation,
        evaluation_file=evaluation_file,
    )
    typer.echo(f"wrote {built} (contains corpus.dump, manifest.json, SHA256SUMS)")


@bundle_app.command("publish-local")
def bundle_publish_local(
    release_id: Annotated[str, typer.Option("--release-id")],
) -> None:
    """Package an already validated local corpus without publishing it."""
    from genereview_link.corpus.bundle_metadata import validate_release_id

    validate_release_id(release_id)
    output = Path(f"genereview-corpus-data-{release_id}")

    built = _build_bundle(output=output, release_id=release_id, skip_validation=False)
    typer.echo(f"local bundle prepared: {built}")


@bundle_app.command("seal-handoff")
def bundle_seal_handoff(
    source: Annotated[Path, typer.Option("--source")],
    handoff_root: Annotated[Path, typer.Option("--handoff-root")],
    publisher_tool: Annotated[Path, typer.Option("--publisher-tool")],
) -> None:
    """Seal an exact local data-only bundle; this command never publishes it."""
    from genereview_link.corpus.handoff import HandoffError, seal_handoff, verify_data_only_bundle

    try:
        verify_data_only_bundle(source)
        sealed = seal_handoff(source, handoff_root, publisher_tool=publisher_tool)
    except HandoffError as error:
        typer.echo(f"handoff refused: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(sealed.object_id)


@bundle_app.command("publish-handoff")
def bundle_publish_handoff(
    handoff_root: Annotated[Path, typer.Option("--handoff-root")],
    object_id: Annotated[str, typer.Option("--object-id")],
    rights_record: Annotated[Path, typer.Option("--rights-record")],
) -> None:
    """Run the privileged rights gate for a sealed handoff before publication."""
    from genereview_link.corpus.handoff import HandoffError, prepare_publish_handoff

    try:
        sealed = prepare_publish_handoff(handoff_root, object_id, rights_record)
    except HandoffError as error:
        typer.echo(f"publish handoff refused: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(
        f"rights-bound handoff ready for an external privileged publisher: {sealed.object_id}"
    )


model_app = typer.Typer(name="model", help="Reviewed embedding model artifact operations.")
app.add_typer(model_app)


@model_app.command("stage")
def model_stage(
    output: Annotated[Path, typer.Option("--output", help="Directory to stage the model into.")],
) -> None:
    """Download the pinned embedding model and prove it against the reviewed identity.

    Maintainer/CI tooling, and the only place the model is fetched. Copy the result to the
    server's seed directory; the serving container never reaches the network.
    """
    from genereview_link.corpus.model_stage import ModelStageError, stage_model

    try:
        staged = stage_model(output)
    except ModelStageError as error:
        typer.echo(f"model staging refused: {error}", err=True)
        raise typer.Exit(1) from error
    for member, digest in sorted(staged.items()):
        typer.echo(f"{digest}  {member}")


@app.command("init")
def init_cmd() -> None:
    """Materialise the reviewed model, then restore the reviewed corpus.

    This is the entry point of the no-egress `genereview-corpus-restore` init sidecar. It
    is ONE command because the fleet deployment gate grants the seed bind mount to exactly
    one init service, so both staged artifacts must be materialised by the same container.
    Model first: it is the cheaper failure, and an operator debugging a refused deploy
    should learn about a bad model before waiting out a corpus restore.
    """
    from genereview_link.config import settings
    from genereview_link.db.model_seed import ModelSeedError, materialize_model

    try:
        staged = materialize_model(Path(settings.MODEL_SEED_PATH), Path(settings.MODEL_DIR))
    except ModelSeedError as exc:
        typer.echo(f"model materialisation refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    logger.info("embedding model materialised", members=sorted(staged))
    corpus_restore()


corpus_app = typer.Typer(name="corpus", help="Immutable corpus artifact operations.")
app.add_typer(corpus_app)


@corpus_app.command("restore")
def corpus_restore() -> None:
    """Restore the reviewed, data-only corpus artifact into an empty database.

    This is the entry point of the no-egress `genereview-corpus-restore` init sidecar. It
    is the ONLY path by which corpus data enters PostgreSQL, and it never reaches the
    network: the artifact is already on disk, read-only, and is proven against the digest
    committed in this repository before it is opened.

    Order matters. The schema is created by the reviewed in-repo migrations FIRST; the
    artifact then contributes table data only, verified data-only, loaded atomically by an
    unprivileged role. The vector index is built afterwards from in-repo code, so no index
    definition is ever taken from the artifact either.
    """
    from genereview_link.config import settings
    from genereview_link.corpus.readiness import (
        ReadinessError,
        require_release_readiness,
        write_release_readiness,
    )
    from genereview_link.db.indexes import build_hnsw_index
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool
    from genereview_link.db.restore import (
        ArchivePolicyError,
        assert_data_only_archive,
        ensure_restore_role,
        extract_bundle,
        read_archive_entries,
        restore_data_only,
        seed_identity_mode,
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            applied = await apply_control_migrations(pool)
            if applied:
                logger.info("applied control migrations", versions=applied)
            applied = await apply_data_migrations(pool, schema="genereview")
            if applied:
                logger.info("applied data migrations", versions=applied)

            identity_mode = seed_identity_mode(
                settings.CORPUS_BUNDLE_SHA256,
                settings.CORPUS_DUMP_SHA256,
                settings.CORPUS_MANIFEST_SHA256,
                settings.CORPUS_CHECKSUMS_SHA256,
            )

            active = await pool.fetchval(
                "select version from public.genereview_corpus_version where is_active"
            )
            if active:
                if identity_mode == "direct":
                    await require_release_readiness(
                        pool,
                        release_tag=settings.CORPUS_RELEASE_TAG,
                        artifact_digest=settings.CORPUS_DUMP_SHA256,
                        manifest_digest=settings.CORPUS_MANIFEST_SHA256,
                        checksums_digest=settings.CORPUS_CHECKSUMS_SHA256,
                    )
                    logger.info(
                        "active direct corpus and verified-v1 readiness present", version=active
                    )
                else:
                    logger.info(
                        "active legacy corpus retained without controller readiness", version=active
                    )
                return

            bundle = extract_bundle(
                Path(settings.CORPUS_SEED_PATH),
                Path(settings.CORPUS_RESTORE_DIR) / "bundle",
                expected_sha256=settings.CORPUS_DUMP_SHA256 or settings.CORPUS_BUNDLE_SHA256,
                expected_manifest_sha256=settings.CORPUS_MANIFEST_SHA256,
                expected_checksums_sha256=settings.CORPUS_CHECKSUMS_SHA256,
            )
            assert_data_only_archive(read_archive_entries(bundle.dump))
            logger.info("corpus archive verified data-only", version=bundle.corpus_version)

            await ensure_restore_role(
                pool,
                settings.RESTORE_ROLE,
                settings.RESTORE_DATABASE_URL,
                owner_url=settings.DATABASE_URL,
            )
            restore_url = settings.RESTORE_DATABASE_URL
            if not restore_url:
                raise ArchivePolicyError(
                    "RESTORE_DATABASE_URL is required: the artifact is never loaded by a superuser"
                )
            restore_data_only(bundle.dump, database_url=restore_url)
            await build_hnsw_index(pool, schema="genereview")

            restored = await pool.fetchval(
                "select version from public.genereview_corpus_version where is_active"
            )
            if not restored:
                raise ArchivePolicyError("restore completed with no active corpus version")
            if identity_mode == "direct":
                if bundle.manifest.get("manifest_version") != "3":
                    raise ReadinessError("direct corpus release requires a manifest-v3 identity")
                await write_release_readiness(
                    pool,
                    bundle.manifest,
                    artifact_digest=f"sha256:{bundle.dump_sha256}",
                    manifest_digest=settings.CORPUS_MANIFEST_SHA256,
                    checksums_digest=settings.CORPUS_CHECKSUMS_SHA256,
                    release_tag=settings.CORPUS_RELEASE_TAG,
                )
            else:
                logger.info("legacy corpus restored without a verified-v1 readiness claim")
            logger.info("corpus restored", version=restored)
        finally:
            await pool.close()

    try:
        asyncio.run(run())
    except (ArchivePolicyError, ReadinessError) as exc:
        typer.echo(f"corpus restore refused: {exc}", err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
