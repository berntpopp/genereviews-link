"""Federation identity: ``serverInfo.name`` and the README's claim about it.

``serverInfo.name`` is load-bearing, not cosmetic. Under MCP Transport & Session
Standard v1 the canonical value is ``genereviews-link`` (set at
``genereview_link/server_manager.py`` in ``FastMCP.from_fastapi(name=...)``, and
asserted against a live server by ``tests/conformance/conformance.py``). The
``genefoundry-router`` registry carries ``server_name: null`` for this backend,
meaning it trusts the name the server actually reports — a silent drift here
breaks federation.

The README states that value in prose. A hand-typed fact that no machine checks
will rot: it did, and shipped ``GeneReview Link Tool`` (the pre-standard name,
removed in c59ad42) long after the code had moved on. So this module pins BOTH
sides and pins them TO EACH OTHER, per the GeneFoundry README Standard v1.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

# The ratified name. Changing this is an intentional, breaking federation change:
# update the router registry and tests/conformance/ in the same commit.
CANONICAL_SERVER_NAME = "genereviews-link"

_README = Path(__file__).resolve().parents[2] / "README.md"
# Matches the README's identity sentence, e.g. "`serverInfo.name` is `genereviews-link`;"
_README_CLAIM_RE = re.compile(r"`serverInfo\.name`\s+is\s+`([^`]+)`")


async def _mcp_name() -> str:
    """Build the MCP facade exactly as the server does and read its advertised name."""
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(ServerConfig())
    mcp = await mgr.create_mcp_server(app, ServerConfig())
    return str(mcp.name)


@pytest.mark.asyncio
async def test_server_info_name_is_canonical() -> None:
    """The facade advertises the fleet-canonical serverInfo.name."""
    assert await _mcp_name() == CANONICAL_SERVER_NAME


def test_readme_states_the_server_info_name() -> None:
    """The README makes the identity claim at all (guards against silent deletion)."""
    match = _README_CLAIM_RE.search(_README.read_text(encoding="utf-8"))
    assert match is not None, (
        "README.md must state the federation identity as "
        "'`serverInfo.name` is `<name>`' — the router registry depends on it"
    )


@pytest.mark.asyncio
async def test_readme_server_info_name_matches_the_code() -> None:
    """The name the README prints is the name the server actually reports."""
    match = _README_CLAIM_RE.search(_README.read_text(encoding="utf-8"))
    assert match is not None, "README.md does not state `serverInfo.name`"
    documented = match.group(1)
    actual = await _mcp_name()
    assert documented == actual, (
        f"README.md says serverInfo.name is {documented!r} but the server "
        f"advertises {actual!r} — fix the README, not this test"
    )
