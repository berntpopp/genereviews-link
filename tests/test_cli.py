"""Tests for the Typer-based CLI."""

from typer.testing import CliRunner

from genereview_link.cli import LogLevel, Transport, app, build_config
from genereview_link.config import ServerConfig

runner = CliRunner()


class TestServeHelp:
    def test_help_lists_serve_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "serve" in result.stdout

    def test_serve_help_lists_all_flags(self) -> None:
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--transport",
            "--host",
            "--port",
            "--mcp-path",
            "--disable-docs",
            "--log-level",
            "--dev",
        ):
            assert flag in result.stdout


class TestBuildConfig:
    def test_defaults(self) -> None:
        config = build_config()
        assert isinstance(config, ServerConfig)
        assert config.transport == "unified"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.mcp_path == "/mcp"
        assert config.enable_docs is True
        assert config.log_level == "INFO"

    def test_explicit_overrides(self) -> None:
        config = build_config(
            transport=Transport.stdio,
            host="0.0.0.0",  # noqa: S104
            port=9000,
            mcp_path="/api/mcp",
            disable_docs=True,
            log_level=LogLevel.debug,
        )
        assert config.transport == "stdio"
        assert config.host == "0.0.0.0"  # noqa: S104
        assert config.port == 9000
        assert config.mcp_path == "/api/mcp"
        assert config.enable_docs is False
        assert config.log_level == "DEBUG"

    def test_disable_docs_inverts_enable_docs(self) -> None:
        assert build_config(disable_docs=False).enable_docs is True
        assert build_config(disable_docs=True).enable_docs is False


class TestInvalidInput:
    def test_invalid_transport_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--transport", "invalid"])
        assert result.exit_code != 0

    def test_invalid_log_level_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--log-level", "TRACE"])
        assert result.exit_code != 0

    def test_non_numeric_port_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--port", "abc"])
        assert result.exit_code != 0
