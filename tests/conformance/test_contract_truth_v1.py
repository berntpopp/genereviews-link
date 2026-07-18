"""Contract Truth v1 gate against the live GeneReviews MCP registry."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

EXPECTED_HELPER_SHA256 = "e6c12b087c8231f5324c6388abd01afaeffa305a84d0b7c0e3629e17993d3674"


async def test_documentation_matches_live_mcp_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Lint repository documentation against the production MCP registry."""
    helper_path = Path(__file__).with_name("contract_truth.py")
    pin_path = Path(__file__).with_name("contract_truth.sha256")

    vendored_pin = pin_path.read_text(encoding="utf-8").strip()
    assert vendored_pin == EXPECTED_HELPER_SHA256
    assert sha256(helper_path.read_bytes()).hexdigest() == vendored_pin

    from .contract_truth import (
        active_markdown_files,
        historical_markdown_files,
        lint_repository,
    )

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DEBUG_RANKING_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)

    from genereview_link.config import ServerConfig
    from genereview_link.server_manager import UnifiedServerManager

    manager = UnifiedServerManager()
    app = manager.create_fastapi_app(ServerConfig())
    tools = await manager.create_mcp_server(app, ServerConfig())
    registered_tools = await tools.list_tools()
    assert registered_tools, "the live MCP registry must not be empty"

    catalog: dict[str, dict[str, object]] = {}
    for tool in registered_tools:
        assert isinstance(tool.parameters, dict)
        catalog[tool.name] = {"inputSchema": tool.parameters}

    repo_root = Path(__file__).resolve().parents[2]
    assert active_markdown_files(repo_root), "active Markdown discovery must not be empty"
    assert historical_markdown_files(repo_root), "historical Markdown discovery must not be empty"

    findings = lint_repository(repo_root, catalog)
    rendered = "\n".join(
        f"{finding.path}:{finding.line}: {finding.rule}: {finding.message}" for finding in findings
    )
    assert not findings, rendered
