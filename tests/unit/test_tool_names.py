"""Tool-name compliance with the GeneFoundry Tool-Naming Standard v1.1.

Every registered tool must be unprefixed, snake_case, <= 50 chars, and start with
a canonical verb so it composes cleanly behind the ``genefoundry-router`` gateway,
which mounts this server under the ``genereviews`` namespace (tools surface as
``genereviews_<tool>``). Guards against future drift. See issue
berntpopp/genereviews-link#67.

**Ratified verb canon (v1.1):**

  Tier-1 (universal read/query): get, search, list, resolve, find, compare,
  compute, map
  Tier-2 (sanctioned action/compute): predict, annotate, recode, liftover,
  analyze, score, submit, export, generate, download

**Ops/meta carve-out (Standard v1.1 §ops/meta):** tools tagged ``ops`` or
``meta`` skip the verb rule (charset/length/no-self-prefix still apply).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
# Tier-1: universal read/query canon (Tool-Naming Standard v1.1, ratified 2026-06-30)
_CANONICAL_VERBS = frozenset(
    {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}
)
# Tier-2: sanctioned domain action/compute verbs (v1.1)
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)
_NAMESPACE = "genereviews"

# Domain tags every surfaced tool must carry so the gateway can filter/curate the
# toolset (Tool-Naming Standard v1, rule 6). Mirrors the assignment in
# ``genereview_link.mcp.error_passthrough.DOMAIN_TAGS``.
_DOMAIN_TAGS = frozenset({"gene", "literature", "meta"})


async def _list_tools() -> list[Any]:
    """Build the MCP facade the same way the server does and return its tools."""
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(ServerConfig())
    mcp = await mgr.create_mcp_server(app, ServerConfig())
    return await mcp.list_tools()


@pytest.mark.asyncio
async def test_tool_names_conform_to_standard_v1() -> None:
    tools = await _list_tools()
    names = sorted(t.name for t in tools)
    assert names, "no tools registered on the facade"
    _all_verbs = _CANONICAL_VERBS | _TIER2_VERBS
    for tool in tools:
        name = tool.name
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace "
            "token — the gateway adds it"
        )
        # Ops/meta carve-out: tools tagged 'ops' or 'meta' skip the verb rule
        tool_tags = set(getattr(tool, "tags", set()) or set())
        if tool_tags & {"ops", "meta"}:
            continue
        assert name.split("_", 1)[0] in _all_verbs, (
            f"{name!r} must start with a Tier-1 or Tier-2 canonical verb "
            f"{sorted(_all_verbs)}, or carry an 'ops'/'meta' tag"
        )


@pytest.mark.asyncio
async def test_tools_carry_domain_tags() -> None:
    """Every surfaced tool advertises at least one canonical domain tag so the
    gateway can filter/curate the toolset (standard rule 6)."""
    tools = await _list_tools()
    for tool in tools:
        tags = set(getattr(tool, "tags", set()) or set())
        domain = tags & _DOMAIN_TAGS
        assert domain, (
            f"{tool.name!r} must carry at least one domain tag from "
            f"{sorted(_DOMAIN_TAGS)}; got {sorted(tags)}"
        )
