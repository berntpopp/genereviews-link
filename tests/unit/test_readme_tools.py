"""The README '## Tools' table must match the server's registered tools exactly.

GeneFoundry README Standard v1, rule 6: the tool table is machine-verified, not
hand-maintained. Adding, renaming, or removing a tool without updating the README
fails CI — which is what stops the table drifting.

The live tool list is obtained the same way ``test_tool_names.py`` does (build the
real MCP facade and call ``list_tools()``); it is never hardcoded here, or this test
would guard nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

README = Path(__file__).resolve().parents[2] / "README.md"

# A table row: | `tool_name` | Purpose |
_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


async def _list_tools() -> list[Any]:
    """Build the MCP facade the same way the server does and return its tools."""
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(ServerConfig())
    mcp = await mgr.create_mcp_server(app, ServerConfig())
    return await mcp.list_tools()


def _readme_tool_table() -> list[str]:
    """Parse the tool names out of the README's '## Tools' section."""
    lines = README.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Tools")
    except StopIteration:  # pragma: no cover - defended by check_readme.py
        pytest.fail("README.md has no '## Tools' section")

    end = next(
        (i for i, ln in enumerate(lines[start + 1 :], start + 1) if ln.startswith("## ")),
        len(lines),
    )

    return [m.group(1) for ln in lines[start:end] if (m := _ROW_RE.match(ln))]


@pytest.mark.asyncio
async def test_readme_tool_table_matches_registered_tools() -> None:
    documented = _readme_tool_table()
    assert documented, "no tool rows parsed from the README '## Tools' table"

    registered = {t.name for t in await _list_tools()}
    assert registered, "no tools registered on the facade"

    documented_set = set(documented)

    missing = registered - documented_set
    extra = documented_set - registered
    assert not missing, (
        f"tools registered on the server but absent from the README '## Tools' table: "
        f"{sorted(missing)} — add a row (README Standard v1, rule 6)"
    )
    assert not extra, (
        f"tools listed in the README '## Tools' table but not registered on the server: "
        f"{sorted(extra)} — the table has drifted"
    )
    assert documented_set == registered


@pytest.mark.asyncio
async def test_readme_tool_table_has_no_duplicate_rows() -> None:
    documented = _readme_tool_table()
    duplicates = sorted({name for name in documented if documented.count(name) > 1})
    assert not duplicates, f"duplicate rows in the README '## Tools' table: {duplicates}"
