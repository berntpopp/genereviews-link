"""
Tests for the CLI interface and argument parsing.

These tests ensure the command-line interface works correctly
with various argument combinations and configurations.
"""

import argparse

import pytest

from genereview_link.cli import create_config_from_args, create_parser
from genereview_link.config import ServerConfig


class TestCLIParser:
    """Test CLI argument parsing functionality."""

    def test_create_parser_default_values(self):
        """Test parser creates with correct default values."""
        parser = create_parser()

        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.description == "GeneReview Link Unified Server"

    def test_parse_default_arguments(self):
        """Test parsing with default arguments."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.transport == "unified"
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.mcp_path == "/mcp"
        assert args.log_level == "INFO"
        assert args.disable_docs is False
        assert args.dev is False

    def test_parse_unified_transport(self):
        """Test parsing unified transport mode."""
        parser = create_parser()
        args = parser.parse_args(["--transport", "unified"])

        assert args.transport == "unified"

    def test_parse_http_transport(self):
        """Test parsing HTTP-only transport mode."""
        parser = create_parser()
        args = parser.parse_args(["--transport", "http"])

        assert args.transport == "http"

    def test_parse_stdio_transport(self):
        """Test parsing STDIO transport mode."""
        parser = create_parser()
        args = parser.parse_args(["--transport", "stdio"])

        assert args.transport == "stdio"

    def test_parse_custom_host_port(self):
        """Test parsing custom host and port."""
        parser = create_parser()
        args = parser.parse_args(["--host", "0.0.0.0", "--port", "9000"])  # noqa: S104

        assert args.host == "0.0.0.0"  # noqa: S104
        assert args.port == 9000

    def test_parse_custom_mcp_path(self):
        """Test parsing custom MCP path."""
        parser = create_parser()
        args = parser.parse_args(["--mcp-path", "/api/mcp"])

        assert args.mcp_path == "/api/mcp"

    def test_parse_log_levels(self):
        """Test parsing different log levels."""
        parser = create_parser()

        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            args = parser.parse_args(["--log-level", level])
            assert args.log_level == level

    def test_parse_disable_docs_flag(self):
        """Test parsing disable docs flag."""
        parser = create_parser()
        args = parser.parse_args(["--disable-docs"])

        assert args.disable_docs is True

    def test_parse_dev_flag(self):
        """Test parsing development mode flag."""
        parser = create_parser()
        args = parser.parse_args(["--dev"])

        assert args.dev is True

    def test_parse_combined_arguments(self):
        """Test parsing multiple arguments together."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "--transport",
                "http",
                "--host",
                "localhost",
                "--port",
                "8080",
                "--mcp-path",
                "/mcp-custom",
                "--log-level",
                "DEBUG",
                "--disable-docs",
                "--dev",
            ]
        )

        assert args.transport == "http"
        assert args.host == "localhost"
        assert args.port == 8080
        assert args.mcp_path == "/mcp-custom"
        assert args.log_level == "DEBUG"
        assert args.disable_docs is True
        assert args.dev is True

    def test_invalid_transport_mode(self):
        """Test error handling for invalid transport mode."""
        parser = create_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--transport", "invalid"])

    def test_invalid_log_level(self):
        """Test error handling for invalid log level."""
        parser = create_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--log-level", "INVALID"])

    def test_invalid_port_type(self):
        """Test error handling for invalid port type."""
        parser = create_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--port", "not-a-number"])


class TestConfigFromArgs:
    """Test configuration creation from parsed arguments."""

    def test_create_config_default_args(self):
        """Test config creation with default arguments."""
        parser = create_parser()
        args = parser.parse_args([])
        config = create_config_from_args(args)

        assert isinstance(config, ServerConfig)
        assert config.transport == "unified"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.mcp_path == "/mcp"
        assert config.enable_docs is True
        assert config.log_level == "INFO"

    def test_create_config_custom_args(self):
        """Test config creation with custom arguments."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "--transport",
                "stdio",
                "--host",
                "0.0.0.0",  # noqa: S104
                "--port",
                "9000",
                "--mcp-path",
                "/api/mcp",
                "--log-level",
                "DEBUG",
                "--disable-docs",
            ]
        )
        config = create_config_from_args(args)

        assert config.transport == "stdio"
        assert config.host == "0.0.0.0"  # noqa: S104
        assert config.port == 9000
        assert config.mcp_path == "/api/mcp"
        assert config.enable_docs is False
        assert config.log_level == "DEBUG"

    def test_create_config_enable_docs_logic(self):
        """Test enable_docs logic (inverted from disable_docs)."""
        parser = create_parser()

        # Test default (docs enabled)
        args = parser.parse_args([])
        config = create_config_from_args(args)
        assert config.enable_docs is True

        # Test with --disable-docs (docs disabled)
        args = parser.parse_args(["--disable-docs"])
        config = create_config_from_args(args)
        assert config.enable_docs is False
