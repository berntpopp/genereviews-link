#!/usr/bin/env python
"""MCP (Model Context Protocol) server for GeneReview Link.

Wraps the FastAPI application to provide MCP protocol compatibility
for integration with Claude and other MCP-compatible clients.
"""
import os
import logging
import sys
from contextlib import redirect_stdout
from io import StringIO

# MUST be set before any imports that use settings
# Completely disable logging for MCP compatibility (stdout reserved for JSON protocol)
os.environ["LOG_LEVEL"] = "CRITICAL"

# Configure logging to stderr and set to highest level to suppress all output
logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)

# Redirect all stdout during import to prevent any contamination
original_stdout = sys.stdout
sys.stdout = StringIO()

try:
    # Import after environment setup and logging configuration
    from fastmcp import FastMCP
    from server import app
finally:
    # Restore stdout for MCP JSON protocol
    sys.stdout = original_stdout

# Disable all logging to prevent stdout contamination
logging.getLogger().setLevel(logging.CRITICAL)
logging.getLogger("fastmcp").setLevel(logging.CRITICAL)
logging.getLogger("genereview_link").setLevel(logging.CRITICAL)

# Define a more user-friendly name for the tool
MCP_CUSTOM_NAMES = {
    "get_genereview_summary": "get_genereview_summary",
}

# Redirect stdout during FastMCP initialization to prevent banner output
with redirect_stdout(StringIO()):
    mcp = FastMCP.from_fastapi(
        app=app,
        name="GeneReview Link Tool",
        mcp_names=MCP_CUSTOM_NAMES,
    )

if __name__ == "__main__":
    # Note: FastMCP banner is sent to stderr, not stdout, so it shouldn't 
    # interfere with JSON protocol on stdout
    mcp.run()
