"""Adversarial tests (F-13 gate): the three remaining schema-name interpolation
sites -- ``atomic_swap`` and ``cleanup_old`` in corpus/pipeline.py and the CLI
``db reset`` loop -- must route every schema name through the strict identifier
validator and fail closed *before* the malicious identifier reaches dynamic SQL.

The connection is faked so we can prove no destructive ``alter``/``drop`` SQL is
ever executed once a hostile schema name is in play.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from genereview_link.corpus import pipeline

MALICIOUS = "public; DROP TABLE x"


class _AsyncCM:
    """Minimal async context manager yielding a fixed value."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeConn:
    """Records executed SQL; never raises on its own so a missing validator is
    observable as *promotion of the malicious statement* rather than an error."""

    def __init__(self, *, active_version: str | None = None, fetch_rows: list[Any] | None = None):
        self._active_version = active_version
        self._fetch_rows = fetch_rows or []
        self.executed: list[str] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "is_active" in query:
            return self._active_version
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        return self._fetch_rows

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        return "OK"

    def transaction(self) -> _AsyncCM:
        return _AsyncCM(None)


def _fake_pool(conn: _FakeConn) -> Any:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _touched_destructive_sql(conn: _FakeConn) -> bool:
    lowered = [q.lower() for q in conn.executed]
    return any(("alter schema" in q or "drop schema" in q) for q in lowered)


# ---- Site 1: corpus/pipeline.py atomic_swap (renames the active schema) ------


async def test_atomic_swap_rejects_malicious_active_version_before_alter() -> None:
    # The active corpus version flows into `genereview_old_<version>` and is
    # interpolated into `alter schema ... rename to "<target>"`.
    conn = _FakeConn(active_version=MALICIOUS)
    with pytest.raises(ValueError):
        await pipeline.atomic_swap(_fake_pool(conn), new_version="genereview_new", chapter_count=1)
    assert not _touched_destructive_sql(conn), conn.executed


# ---- Site 2: corpus/pipeline.py cleanup_old (drops retired old_* schemas) ----


async def test_cleanup_old_rejects_malicious_schema_name_before_drop() -> None:
    # information_schema returns a hostile schema name beyond the retention
    # window; it must be validated before the `drop schema ... cascade`.
    rows = [
        {"schema_name": "genereview_old_a"},
        {"schema_name": "genereview_old_b"},
        {"schema_name": MALICIOUS},
    ]
    conn = _FakeConn(fetch_rows=rows)
    with pytest.raises(ValueError):
        await pipeline.cleanup_old(_fake_pool(conn), retain=2)
    assert not _touched_destructive_sql(conn), conn.executed


# ---- Site 3: cli.py `db reset` old_* drop loop ------------------------------


def test_cli_db_reset_rejects_malicious_schema_name_before_drop(mocker: Any) -> None:
    from typer.testing import CliRunner

    from genereview_link.cli import app

    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()
    fake_conn.fetch = AsyncMock(return_value=[{"schema_name": MALICIOUS}])

    fake_pool = MagicMock()
    fake_pool.close = AsyncMock()
    fake_pool.acquire = MagicMock(return_value=_AsyncCM(fake_conn))

    mocker.patch("genereview_link.db.pool.create_pool", AsyncMock(return_value=fake_pool))
    mocker.patch("genereview_link.db.migrate.apply_control_migrations", AsyncMock(return_value=[]))
    mocker.patch("genereview_link.db.migrate.apply_data_migrations", AsyncMock(return_value=[]))

    result = CliRunner().invoke(app, ["db", "reset", "--yes"])

    assert result.exit_code != 0
    # The hostile identifier never reached a `drop schema` statement.
    executed = [str(c.args[0]) for c in fake_conn.execute.await_args_list]
    assert not any("DROP TABLE" in q for q in executed), executed
    assert "DROP TABLE" not in (result.stdout or "")
