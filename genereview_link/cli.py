"""Typer-based CLI for the GeneReview Link unified server."""

from __future__ import annotations

import asyncio
import sys
from enum import StrEnum
from typing import Annotated

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
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

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


if __name__ == "__main__":
    app()
