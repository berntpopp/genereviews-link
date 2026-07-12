"""Repository policy: all third-party GitHub Actions are immutable SHA pins."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
USES_RE = re.compile(r"^\s*uses:\s*(?P<action>[^\s#]+)(?:\s+#\s*(?P<version>\S.+))?\s*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def test_external_github_actions_are_sha_pinned_with_version_comments() -> None:
    violations: list[str] = []
    for path in sorted((REPO_ROOT / ".github").rglob("*")):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(line)
            if match is None:
                continue
            action = match.group("action")
            if action.startswith("./") or action.startswith("docker://"):
                continue
            if "@" not in action:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: missing @ref")
                continue
            _, ref = action.rsplit("@", maxsplit=1)
            if not SHA_RE.fullmatch(ref):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: ref is not a 40-hex SHA"
                )
            if not match.group("version"):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: missing version comment"
                )

    assert not violations, "\n".join(violations)
