"""Command line interface for the GeneReview Link unified server."""

import argparse
from .config import ServerConfig


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for the server."""
    parser = argparse.ArgumentParser(description="GeneReview Link Unified Server")
    parser.add_argument(
        "--transport",
        choices=["unified", "http", "stdio"],
        default="unified",
        help="Transport mode",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--mcp-path", default="/mcp", help="MCP endpoint path")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level",
    )
    parser.add_argument(
        "--disable-docs", action="store_true", help="Disable API documentation"
    )
    parser.add_argument(
        "--dev", action="store_true", help="Development mode with auto-reload"
    )
    return parser


def create_config_from_args(args: argparse.Namespace) -> ServerConfig:
    """Create ServerConfig from parsed command line arguments."""
    return ServerConfig(
        transport=args.transport,
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
        enable_docs=not args.disable_docs,
        log_level=args.log_level,
    )
