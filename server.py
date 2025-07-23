#!/usr/bin/env python
"""Unified GeneReview Link server with multiple transport support."""

import asyncio
import sys
import uvicorn

from genereview_link.cli import create_parser, create_config_from_args
from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.logging_config import configure_structlog, get_logger

# Configure logging early
configure_structlog()
logger = get_logger("server.main")


def main() -> None:
    """Start the GeneReview Link unified server."""
    parser = create_parser()
    args = parser.parse_args()

    # Create config from args and environment
    config = create_config_from_args(args)

    # Special handling for dev mode with uvicorn reload
    if args.dev and config.transport != "stdio":
        logger.info("Running in development mode with auto-reload.")
        uvicorn.run(
            "server:app",
            host=config.host,
            port=config.port,
            reload=True,
            log_config=None,  # Allow structlog to handle logging
        )
        return

    try:
        manager = UnifiedServerManager()
        asyncio.run(manager.start_server(config))
    except (ValueError, asyncio.CancelledError) as e:
        logger.error("Server startup failed", error=str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user.")
        sys.exit(0)


# This is needed for uvicorn --reload to work
manager = UnifiedServerManager()
config = ServerConfig()  # Default config for initial load
app = asyncio.run(manager.create_fastapi_app(config))


if __name__ == "__main__":
    main()
