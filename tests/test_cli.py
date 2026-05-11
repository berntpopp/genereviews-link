"""Tests for the Typer-based CLI."""

from typer.main import get_command
from typer.testing import CliRunner

from genereview_link.cli import LogLevel, Transport, app, build_config
from genereview_link.config import ServerConfig

runner = CliRunner()


class TestServeHelp:
    def test_help_lists_serve_command(self) -> None:
        # Inspect the registered Click command instead of the rendered help
        # text. Help rendering goes through Rich, whose ANSI styling and
        # terminal-width-dependent wrapping make substring assertions brittle
        # across platforms (this test was previously flaky on Linux CI).
        command = get_command(app)
        assert "serve" in command.commands  # type: ignore[attr-defined]

    def test_serve_help_lists_all_flags(self) -> None:
        command = get_command(app)
        serve = command.commands["serve"]  # type: ignore[attr-defined]
        registered_flags = {opt for param in serve.params for opt in param.opts}
        for flag in (
            "--transport",
            "--host",
            "--port",
            "--mcp-path",
            "--disable-docs",
            "--log-level",
            "--dev",
        ):
            assert flag in registered_flags


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
