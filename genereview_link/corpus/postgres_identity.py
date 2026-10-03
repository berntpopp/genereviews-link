"""Closed-world PostgreSQL and pgvector identities supported by corpus releases."""

from __future__ import annotations

from typing import Any

SUPPORTED_PGVECTOR_RUNTIME_VERSIONS = frozenset({"0.8.2", "0.8.6"})

# A corpus recorded under 0.8.2 can be restored by the current 0.8.6 server image.
# A bundle emitted under 0.8.6 must not be restored onto the older 0.8.2 runtime.
SUPPORTED_PGVECTOR_RESTORE_PAIRS = frozenset(
    {("0.8.2", "0.8.2"), ("0.8.2", "0.8.6"), ("0.8.6", "0.8.6")}
)


def is_supported_pgvector_runtime(version: object) -> bool:
    """Return whether *version* is an explicitly reviewed live pgvector runtime."""
    return isinstance(version, str) and version in SUPPORTED_PGVECTOR_RUNTIME_VERSIONS


def is_supported_pgvector_restore(source: object, target: object) -> bool:
    """Return whether a bundle exported by *source* may cold-restore onto *target*."""
    return (
        isinstance(source, str)
        and isinstance(target, str)
        and (
            source,
            target,
        )
        in SUPPORTED_PGVECTOR_RESTORE_PAIRS
    )


def require_supported_pgvector_restore(source: object, target: object) -> None:
    """Raise when the staged bundle cannot be safely used by the target runtime."""
    if not is_supported_pgvector_restore(source, target):
        raise ValueError(f"unsupported pgvector restore pair: {source!r} -> {target!r}")


async def require_database_pgvector_restore(pool: Any, source: object) -> None:
    """Check the staged corpus against the already-migrated target database."""
    target = str(
        await pool.fetchval("select extversion from pg_extension where extname = 'vector'") or ""
    )
    require_supported_pgvector_restore(source, target)
