"""Dependency-free entry point for privileged sealed-handoff verification.

This package path intentionally imports only Python-standard-library corpus modules.
The privileged runner installs the sealed wheel without dependencies and imports this
module from an explicitly verified target directory under ``python -I``.
"""

from __future__ import annotations

from genereview_link.corpus.handoff import prepare_publish_handoff

__all__ = ["prepare_publish_handoff"]
