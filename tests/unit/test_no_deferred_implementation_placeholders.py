"""Guard: no `pass` statement may defer its own implementation (#145 action item 3).

`ingest/scheduler.py` used to end in `if settings.AUTO_PULL_RELEASES: pass`, commented
"implementation extends Task 6.3" -- a placeholder that reached production and silently
did nothing for ~3.7 months while `/health` kept reporting `healthy`, because nothing
checked corpus age. This scans every production source module (`genereview_link/`) for the
same shape of comment attached to a `pass` statement, so the next one fails a test instead
of reaching production unnoticed. Cheap by design, and it generalises past this one file.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches e.g. `pass  # implementation extends Task 6.3` -- a bare `pass` whose own
# trailing comment says the real behavior is deferred, rather than a `pass` that is a
# legitimate no-op (an empty `except` branch, an abstract Protocol stub, etc.).
_PLACEHOLDER = re.compile(
    r"^\s*pass\b.*#.*\b(extends|todo|not\s+implemented|future\s+work|placeholder)\b",
    re.IGNORECASE,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "genereview_link"


def _source_files() -> list[Path]:
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


def test_no_deferred_implementation_pass_statements() -> None:
    offenders = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _PLACEHOLDER.match(line):
                relative = path.relative_to(_PACKAGE_ROOT.parent)
                offenders.append(f"{relative}:{lineno}: {line.strip()}")
    assert not offenders, (
        "a `pass` statement defers its own implementation, per its own trailing comment; "
        "implement it or remove the branch instead of shipping a stub that can report "
        "healthy while doing nothing (see genereview_link/ingest/scheduler.py's docstring "
        "for exactly how that went wrong in #145):\n" + "\n".join(offenders)
    )


def test_scan_matches_the_exact_historical_offender_shape() -> None:
    """Guard the guard: prove the regex actually catches #145's own bug, and does not
    fire on the module docstring that now narrates it as history."""
    assert _PLACEHOLDER.match("    pass  # implementation extends Task 6.3")
    assert not _PLACEHOLDER.match(
        '    with the comment "implementation extends Task 6.3 bootstrap into a hot-swap path".'
    )
