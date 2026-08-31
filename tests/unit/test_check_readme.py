"""README identity must survive an isolated git worktree name."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_repo_slug_comes_from_git_remote_not_worktree_leaf() -> None:
    spec = importlib.util.spec_from_file_location("check_readme", ROOT / "scripts/check_readme.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.repo_slug() == "genereviews-link"
