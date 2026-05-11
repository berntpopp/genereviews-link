#!/usr/bin/env python
"""Unified GeneReview Link server entry point."""

import asyncio

from genereview_link.cli import app as cli_app
from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog
from genereview_link.server_manager import UnifiedServerManager

configure_structlog()


# Module-level ASGI app for uvicorn --reload and gunicorn
manager = UnifiedServerManager()
config = ServerConfig()
app = asyncio.run(manager.create_fastapi_app(config))


def main() -> None:
    """CLI entry point."""
    cli_app()


if __name__ == "__main__":
    main()
