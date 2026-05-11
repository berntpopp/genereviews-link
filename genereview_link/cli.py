"""Typer-based CLI for the GeneReview Link unified server."""

from __future__ import annotations

import asyncio
import sys
from enum import Enum

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


class Transport(str, Enum):
    """Transport mode for the server."""

    unified = "unified"
    http = "http"
    stdio = "stdio"


class LogLevel(str, Enum):
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
    transport: Transport = typer.Option(
        Transport.unified, "--transport", help="Transport mode"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", help="Port to bind to"),
    mcp_path: str = typer.Option("/mcp", "--mcp-path", help="MCP endpoint path"),
    disable_docs: bool = typer.Option(
        False, "--disable-docs", help="Disable API documentation endpoints"
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.info, "--log-level", help="Log level"
    ),
    dev: bool = typer.Option(
        False, "--dev", help="Development mode with auto-reload"
    ),
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


if __name__ == "__main__":
    app()
