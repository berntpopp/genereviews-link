"""Shared pytest fixtures and process-wide environment for the test suite."""

from __future__ import annotations

import os

# Lock COLUMNS wide and disable ANSI color before any test imports Typer
# or Rich. CI behavior we are defending against:
#
# 1. CI runs without a TTY; Typer/Rich falls back to a narrow default
#    width that truncates long option names ("--release-id" becomes
#    "--release-...") and breaks substring assertions in test_cli.py.
# 2. GitHub Actions and many other CI runners export FORCE_COLOR=1.
#    Rich then renders option names with the leading dash split into its
#    own ANSI sequence, e.g. "\x1b[36m-\x1b[0m\x1b[36m-release-id\x1b[0m".
#    The literal substring "--release-id" is never present in the
#    rendered output, again breaking the substring assertions.
#
# COLUMNS=200 fixes (1). NO_COLOR=1 + popping FORCE_COLOR fixes (2).
# Local developer overrides (explicitly set COLUMNS or FORCE_COLOR) are
# honored via setdefault for COLUMNS and a guarded pop for FORCE_COLOR.
os.environ.setdefault("COLUMNS", "200")
os.environ.pop("FORCE_COLOR", None)
os.environ["NO_COLOR"] = "1"
