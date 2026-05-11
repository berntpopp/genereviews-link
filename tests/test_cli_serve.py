"""Tests for the body of the ``serve`` Typer command.

These tests patch the boundaries (UnifiedServerManager.start_server and
uvicorn.run) so the CLI body can run end-to-end without actually binding to a
port or spinning up an event loop.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from genereview_link.cli import app

runner = CliRunner()


@pytest.fixture
def mock_manager_start(mocker: MockerFixture) -> Any:
    """Patch ``UnifiedServerManager.start_server`` to a no-op awaitable."""

    async def _noop(self: Any, config: Any) -> None:
        return None

    return mocker.patch(
        "genereview_link.server_manager.UnifiedServerManager.start_server",
        _noop,
    )


class TestServeUnified:
    def test_unified_default_invokes_manager(self, mock_manager_start: Any) -> None:
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0, result.stdout

    def test_unified_with_explicit_flags(self, mock_manager_start: Any) -> None:
        result = runner.invoke(
            app,
            [
                "serve",
                "--transport",
                "unified",
                "--host",
                "127.0.0.1",
                "--port",
                "8123",
                "--mcp-path",
                "/api/mcp",
                "--disable-docs",
                "--log-level",
                "DEBUG",
            ],
        )
        assert result.exit_code == 0, result.stdout

    def test_stdio_transport_invokes_manager(self, mock_manager_start: Any) -> None:
        result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 0, result.stdout


class TestServeDevMode:
    def test_dev_mode_runs_uvicorn(self, mocker: MockerFixture) -> None:
        run_mock = mocker.patch("genereview_link.cli.uvicorn.run")

        # Also patch the manager to be defensive in case dev path falls through.
        async def _noop(self: Any, config: Any) -> None:
            return None

        mocker.patch(
            "genereview_link.server_manager.UnifiedServerManager.start_server",
            _noop,
        )

        result = runner.invoke(app, ["serve", "--dev", "--port", "9001"])
        assert result.exit_code == 0, result.stdout
        run_mock.assert_called_once()
        kwargs = run_mock.call_args.kwargs
        assert kwargs.get("reload") is True
        assert kwargs.get("port") == 9001

    def test_dev_with_stdio_uses_manager_not_uvicorn(self, mocker: MockerFixture) -> None:
        run_mock = mocker.patch("genereview_link.cli.uvicorn.run")

        async def _noop(self: Any, config: Any) -> None:
            return None

        mocker.patch(
            "genereview_link.server_manager.UnifiedServerManager.start_server",
            _noop,
        )

        result = runner.invoke(app, ["serve", "--dev", "--transport", "stdio"])
        assert result.exit_code == 0, result.stdout
        # stdio transport bypasses the dev/uvicorn auto-reload branch.
        run_mock.assert_not_called()


class TestServeErrorPaths:
    def test_value_error_exits_with_code_1(self, mocker: MockerFixture) -> None:
        async def _raise(self: Any, config: Any) -> None:
            raise ValueError("bad transport configuration")

        mocker.patch(
            "genereview_link.server_manager.UnifiedServerManager.start_server",
            _raise,
        )
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 1

    def test_keyboard_interrupt_exits_with_code_0(self, mocker: MockerFixture) -> None:
        async def _interrupt(self: Any, config: Any) -> None:
            raise KeyboardInterrupt

        mocker.patch(
            "genereview_link.server_manager.UnifiedServerManager.start_server",
            _interrupt,
        )
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0
