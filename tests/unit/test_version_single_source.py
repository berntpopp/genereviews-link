"""Guard: pyproject -> installed metadata -> __version__ -> MCP serverInfo are one value.

The MCP ``initialize`` response advertises ``serverInfo.version`` from the FastMCP
instance's ``.version`` attribute. If the server is constructed without an explicit
``version=``, FastMCP falls back to advertising its own framework version (e.g.
``3.2.4``) instead of the ``genereviews-link`` package version. This guard pins all
version sources to a single value so that drift fails loudly.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from genereview_link import __version__
from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

DIST = "genereviews-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


async def test_mcp_server_info_version_matches_package() -> None:
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(ServerConfig())
    mcp = await mgr.create_mcp_server(app, ServerConfig())
    assert mcp.version == __version__
