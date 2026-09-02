"""Tests for the Typer-based CLI."""

from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from genereview_link import cli
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


class TestBundleCommands:
    """Verify bundle subcommands are registered with the expected options.

    Inspects the Click command tree directly rather than the rendered help
    text. CI runs Rich with ANSI styling forced on and a narrow terminal
    width, which both wrap option names and split each leading dash into
    its own escape sequence (`\\x1b[36m-\\x1b[0m\\x1b[36m-release-id\\x1b[0m`).
    Substring assertions against `result.output` then never match. Same
    pattern as `TestServeHelp.test_serve_help_lists_all_flags` above.
    """

    @staticmethod
    def _bundle_subcommand_flags(name: str) -> set[str]:
        bundle_group = get_command(app).commands["bundle"]  # type: ignore[attr-defined]
        subcommand = bundle_group.commands[name]  # type: ignore[attr-defined]
        return {opt for param in subcommand.params for opt in param.opts}

    def test_bundle_validate_command_registered(self) -> None:
        bundle_group = get_command(app).commands["bundle"]  # type: ignore[attr-defined]
        assert "validate" in bundle_group.commands  # type: ignore[attr-defined]

    def test_bundle_build_exposes_release_id_option(self) -> None:
        flags = self._bundle_subcommand_flags("build")
        assert "--release-id" in flags
        assert "--skip-validation" in flags

    def test_bundle_publish_local_command_registered(self) -> None:
        flags = self._bundle_subcommand_flags("publish-local")
        assert "--release-id" in flags
        assert "--upload" not in flags
        assert "--repo" not in flags
        assert "--draft" not in flags

    def test_bundle_build_describes_directory_checksum_contents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = Path("genereview-corpus-data-2026-08-30-r1")
        monkeypatch.setattr(cli, "_build_bundle", lambda **_kwargs: built)

        result = runner.invoke(app, ["bundle", "build", "--release-id", "2026-08-30-r1"])

        assert result.exit_code == 0, result.output
        assert "SHA256SUMS" in result.output
        assert ".sha256" not in result.output

    def test_bundle_publish_local_only_runs_the_local_packager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = Path("genereview-corpus-data-2026-08-30-r1")
        calls: list[dict[str, object]] = []

        def build_bundle(**kwargs: object) -> Path:
            calls.append(kwargs)
            return built

        monkeypatch.setattr(cli, "_build_bundle", build_bundle)
        result = runner.invoke(app, ["bundle", "publish-local", "--release-id", "2026-08-30-r1"])

        assert result.exit_code == 0, result.output
        assert calls == [{"output": built, "release_id": "2026-08-30-r1", "skip_validation": False}]
        assert "local bundle prepared" in result.output

    def test_bundle_publish_local_rejects_upload_option(self) -> None:
        result = runner.invoke(
            app,
            ["bundle", "publish-local", "--release-id", "2026-08-30-r1", "--upload"],
        )

        assert result.exit_code != 0
        assert "No such option" in result.output

    def test_bundle_surface_is_exactly_the_local_build_commands(self) -> None:
        """The CLI can build and validate a bundle locally; it cannot publish one.

        Publication is now an ordinary ``gh release create`` of three files performed by
        the maintainer, so the sealed-handoff commands (``seal-handoff`` and
        ``publish-handoff``) are gone rather than merely unused -- there is no object to
        seal and no privileged rights gate for them to run.
        """
        bundle_group = get_command(app).commands["bundle"]  # type: ignore[attr-defined]

        assert set(bundle_group.commands) == {  # type: ignore[attr-defined]
            "validate",
            "build",
            "publish-local",
        }
