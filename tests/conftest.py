"""Shared pytest fixtures and process-wide environment for the test suite."""

from __future__ import annotations

import os

# Typer/Rich render command help wrapped to terminal width. CI runs with no
# TTY and falls back to a narrow default that truncates long option names
# like "--release-id" to "--release-..." with an ellipsis, breaking
# help-text substring assertions in tests/test_cli.py. Lock the width wide
# so every CliRunner invocation in this suite renders the full option name.
os.environ.setdefault("COLUMNS", "200")
