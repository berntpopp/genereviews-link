"""Migration runner.

Applies SQL migration files in lexical order and records applied versions
in the schema_migrations table. Supports two namespaces:

- ``control`` migrations always apply to the ``public`` schema
- ``data`` migrations apply to a caller-specified target schema
  (typically ``genereview`` or ``genereview_staging``)
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import logging
from types import ModuleType
from typing import Literal

import asyncpg

from genereview_link.db.identifiers import quote_pg_identifier, validate_schema_identifier
from genereview_link.db.migrations import control as control_pkg
from genereview_link.db.migrations import data as data_pkg

logger = logging.getLogger(__name__)


Namespace = Literal["control", "data"]


def _list_sql(pkg: ModuleType) -> list[tuple[str, str]]:
    root = pkg_resources.files(pkg)
    files = sorted(f.name for f in root.iterdir() if f.is_file() and f.name.endswith(".sql"))
    return [
        (name.removesuffix(".sql"), root.joinpath(name).read_text(encoding="utf-8"))
        for name in files
    ]


async def _ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        create schema if not exists public;
        create table if not exists public.schema_migrations (
            namespace   text not null,
            version     text not null,
            applied_at  timestamptz not null default now(),
            primary key (namespace, version)
        )
        """
    )


async def list_applied(pool: asyncpg.Pool, *, namespace: Namespace) -> list[str]:
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        rows = await conn.fetch(
            "select version from public.schema_migrations where namespace = $1 order by version",
            namespace,
        )
    return [row["version"] for row in rows]


async def apply_control_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply control migrations into public; return newly applied versions."""
    applied: list[str] = []
    files = _list_sql(control_pkg)
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        existing = {
            row["version"]
            for row in await conn.fetch(
                "select version from public.schema_migrations where namespace = 'control'"
            )
        }
        for version, sql in files:
            if version in existing:
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (namespace, version) "
                    "values ('control', $1)",
                    version,
                )
            applied.append(version)
            logger.info("applied control migration: %s", version)
    return applied


async def apply_data_migrations(pool: asyncpg.Pool, *, schema: str) -> list[str]:
    """Apply data migrations into the given schema; return newly applied versions.

    Migrations may reference unqualified table names since search_path is set
    to schema,public for the duration of each migration.
    """
    # Fail closed on a hostile schema identifier BEFORE any connection/SQL.
    validate_schema_identifier(schema)
    quoted_schema = quote_pg_identifier(schema)
    applied: list[str] = []
    files = _list_sql(data_pkg)
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        await conn.execute(f"create schema if not exists {quoted_schema}")
        existing = {
            row["version"]
            for row in await conn.fetch(
                "select version from public.schema_migrations "
                "where namespace = 'data' and version like $1",
                f"{schema}:%",
            )
        }
        for version, sql in files:
            qualified = f"{schema}:{version}"
            if qualified in existing:
                continue
            async with conn.transaction():
                await conn.execute(f"set local search_path to {quoted_schema}, public")
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (namespace, version) values ('data', $1)",
                    qualified,
                )
            applied.append(qualified)
            logger.info("applied data migration: %s into %s", version, schema)
    return applied
