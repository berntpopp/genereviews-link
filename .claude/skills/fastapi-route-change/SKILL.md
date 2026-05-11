---
name: fastapi-route-change
description: Use when adding, renaming, or modifying a FastAPI route in genereviews-link.
---

# FastAPI Route Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect existing route modules under `genereview_link/api/routes/` and
   reuse their pattern (router declaration, dependency injection, error
   handling, response_model).
2. Use Pydantic models from `genereview_link/models/` for request and
   response shapes. Add new models there if needed.
3. Raise `DataNotFoundError` from the service layer (genereview_service.py)
   for 404 cases; let the existing exception handler convert it to HTTP 404.
4. Update or add MCP tool naming in `server_manager.create_mcp_server`'s
   `mcp_custom_names` and `mcp_route_maps` if the route should be exposed
   via MCP.
5. Add route-level tests in `tests/test_api_integration.py` and unit tests
   for any new service logic in the appropriate test file.
6. Update `README.md` API table if the public endpoint list changed.
7. Run `make ci-local` before handoff.
