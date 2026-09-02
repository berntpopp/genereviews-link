"""Deterministic, read-only semantic probe of the restored GeneReviews corpus.

The fleet controller (``strato_v6_docker_npm``) execs this module inside the running
application container to observe *what the data actually is*, independently of what the
deployment claims::

    docker compose exec -T genereview-link python -m genereview_link.data_probe

It prints exactly one JSON object with exactly these keys::

    {"data_schema_version": "<str>", "record_count": <int>, "query_result_sha256": "<64 hex>"}

- ``data_schema_version`` is the newest applied *data* migration in
  ``public.schema_migrations`` with its target-schema prefix removed (rows are stored as
  ``genereview:0007_embedding_run_identity``). That is the reviewed identity of the
  ``genereview`` schema itself, so it changes when the schema changes and not when the
  corpus content does -- which is what a compatibility statement needs to mean.
- ``record_count`` counts the primary entity, ``genereview.genereview_passages``.
- ``query_result_sha256`` is the SHA-256 of the UTF-8 text of the canonical first passage
  id (``ORDER BY nbk_id, passage_id LIMIT 1``, the table's primary key order).

Two containers serving the same data release print byte-identical output.

The corpus is a restored database, not a file, so "open it immutably" is a ``READ ONLY``,
``REPEATABLE READ`` transaction: the probe cannot write, and every statement in it sees one
snapshot, so the three answers describe the same instant. It uses only ``DATABASE_URL`` from
the container environment, needs no network beyond the internal database network, and runs
as the image's non-root user.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from typing import Any

__all__ = ["DataProbeError", "main", "probe"]

_SCHEMA_QUERY = (
    "select version from public.schema_migrations "
    "where namespace = 'data' order by version desc limit 1"
)
_COUNT_QUERY = "select count(*) from genereview.genereview_passages"
_FIRST_KEY_QUERY = (
    "select passage_id from genereview.genereview_passages order by nbk_id, passage_id limit 1"
)


class DataProbeError(RuntimeError):
    """The restored corpus cannot answer the reviewed probe query."""


def _bare_schema_version(value: object) -> str:
    """Return ``0007_embedding_run_identity`` from ``genereview:0007_embedding_run_identity``."""
    if not isinstance(value, str) or not value:
        raise DataProbeError("the restored corpus has no applied data schema version")
    return value.rsplit(":", 1)[-1]


async def probe(dsn: str) -> dict[str, Any]:
    """Return the reviewed observation of one restored GeneReviews corpus."""
    import asyncpg

    try:
        connection = await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        raise DataProbeError(f"the corpus database is not reachable: {exc}") from exc
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            schema_version = await connection.fetchval(_SCHEMA_QUERY)
            record_count = await connection.fetchval(_COUNT_QUERY)
            first_key = await connection.fetchval(_FIRST_KEY_QUERY)
    except asyncpg.PostgresError as exc:
        raise DataProbeError(f"the corpus database is not readable: {exc}") from exc
    finally:
        await connection.close()
    if first_key is None or not record_count:
        raise DataProbeError("the restored corpus has no passages to observe")
    return {
        "data_schema_version": _bare_schema_version(schema_version),
        "record_count": int(record_count),
        "query_result_sha256": hashlib.sha256(str(first_key).encode("utf-8")).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    """Print the probe observation as one line of JSON; non-zero on any failure."""
    if argv:
        sys.stderr.write("usage: python -m genereview_link.data_probe\n")
        return 2
    # Imported here so a configuration problem is reported as a probe failure rather than an
    # import-time traceback, and so the module stays importable for unit tests.
    from genereview_link.config import settings

    if not settings.DATABASE_URL:
        sys.stderr.write("DATABASE_URL is not configured\n")
        return 1
    try:
        observation = asyncio.run(probe(settings.DATABASE_URL))
    except DataProbeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    # sys.stdout, not print(): exactly one line of JSON on stdout is the contract the
    # controller parses, and the package forbids bare prints (ruff T20).
    sys.stdout.write(json.dumps(observation, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
