#!/usr/bin/env python
import logging
import sys
from fastmcp import FastMCP
from server import app

# Configure logging to stderr to avoid interfering with MCP's stdout
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# Define a more user-friendly name for the tool
MCP_CUSTOM_NAMES = {
    "get_genereview_summary": "get_genereview_summary",
}

mcp = FastMCP.from_fastapi(
    app=app,
    name="GeneReview Link Tool",
    mcp_names=MCP_CUSTOM_NAMES,
)

if __name__ == "__main__":
    mcp.run()
