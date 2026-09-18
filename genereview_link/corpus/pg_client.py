"""Run PostgreSQL client tools from the exact reviewed PG18 image."""

from __future__ import annotations

import os
import re
from pathlib import Path

PG18_IMAGE = (
    "pgvector/pgvector:0.8.6-pg18@"
    "sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
)
PG_TOOLS = frozenset({"pg_dump", "pg_restore", "psql", "pg_isready"})


class PgClientError(RuntimeError):
    """The reviewed PostgreSQL client contract was not satisfied."""


def build_pg_client_command(
    tool: str,
    arguments: list[str],
    *,
    mounts: tuple[Path, ...] = (),
    network: str = "host",
) -> list[str]:
    if tool not in PG_TOOLS:
        raise PgClientError(f"unsupported PostgreSQL client tool: {tool}")
    if network != "host":
        raise PgClientError("PostgreSQL client container must use the reviewed host network")
    command = ["docker", "run", "--rm", "--network", network]
    command.extend(["--user", f"{os.geteuid()}:{os.getegid()}"])
    for path in mounts:
        resolved = path.resolve()
        command.extend(["--volume", f"{resolved}:{resolved}"])
    command.extend([PG18_IMAGE, tool, *arguments])
    return command


def assert_client_server_match(client_version: str, server_version_num: str) -> None:
    match = re.search(r"\(PostgreSQL\)\s+(\d+)(?:\.|\s|$)", client_version)
    if match is None or not server_version_num.isdigit():
        raise PgClientError("PostgreSQL client/server version identity is malformed")
    client_major = int(match.group(1))
    server_major = int(server_version_num) // 10_000
    if client_major != server_major:
        raise PgClientError(
            f"PostgreSQL client major {client_major} does not match server major {server_major}"
        )


__all__ = [
    "PG18_IMAGE",
    "PgClientError",
    "assert_client_server_match",
    "build_pg_client_command",
]
