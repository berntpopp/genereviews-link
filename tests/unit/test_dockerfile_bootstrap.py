"""Regression test (F-19): reproducible Docker installer bootstrap.

Both the builder and runtime stages previously bootstrapped their installer
tooling with a *floating* lower-bound upgrade (`pip install --upgrade
"pip>=26.1" uv`), so a rebuild could silently pull a newer, unreviewed pip/uv.
The builder must instead COPY a digest-pinned `uv` binary, and no stage may run
an unbounded `pip install --upgrade`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

UV_DIGEST_COPY = (
    "COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab "
    "/uv /usr/local/bin/uv"
)


def _dockerfile() -> str:
    return (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_copies_digest_pinned_uv() -> None:
    assert UV_DIGEST_COPY in _dockerfile(), (
        "builder stage must COPY a digest-pinned uv binary instead of "
        "bootstrapping uv via a floating pip install"
    )


def test_dockerfile_has_no_floating_pip_upgrade() -> None:
    text = _dockerfile()
    assert "pip install --upgrade" not in text, (
        "floating `pip install --upgrade` bootstrap must be removed"
    )
    assert "pip>=26.1" not in text, (
        "floating lower-bound `pip>=26.1` must be replaced by an exact pin"
    )
    assert " uv &&" not in text, "uv must no longer be pip-installed in the builder"
