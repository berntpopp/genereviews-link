#!/usr/bin/env python
"""Backwards-compatible MCP STDIO server for GeneReview Link."""

import asyncio
import sys

from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog
from genereview_link.server_manager import UnifiedServerManager


def main() -> None:
    """Start the STDIO MCP server for AI assistant integration."""
    # Configure minimal logging suitable for STDIO
    configure_structlog()

    try:
        config = ServerConfig(transport="stdio")
        manager = UnifiedServerManager()
        asyncio.run(manager.start_stdio_server(config))
    except Exception as e:
        # stderr-only diagnostic: stdout is reserved for MCP protocol frames
        print(f"MCP server error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
