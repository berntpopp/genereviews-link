---
name: mcp-tool-change
description: Use when adding, renaming, or changing GeneReview-Link MCP tools, resources, or schemas.
---

# MCP Tool Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect `genereview_link/server_manager.py:create_mcp_server` for the
   existing `mcp_custom_names` and `mcp_route_maps` patterns.
2. Keep hosted public tools research-use scoped; do not add clinical
   decision support, destructive cache operations, or broad
   filesystem/network powers.
3. Prefer typed Pydantic input/output models over raw dicts.
4. Update MCP tool name mappings and route-map filters in
   `create_mcp_server`. New REST endpoints should be auto-exposed via
   `FastMCP.from_fastapi`; explicitly exclude any endpoint that should
   not be a tool.
5. Add or update tests that touch the MCP-mounted app path.
6. Update README and AGENTS.md if tool names or scopes change.
7. Run `make ci-local` and a manual stdio smoke test (`make mcp-serve`
   with a JSON-RPC initialize) before handoff.
