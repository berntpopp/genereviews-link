"""Adversarial tests (F-13): the admin --schema value reaches dynamic SQL
identifiers, so it must satisfy a strict PostgreSQL identifier grammar and be
rejected *before* any SQL is executed."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genereview_link.db import migrate
from genereview_link.db.identifiers import (
    quote_pg_identifier,
    validate_schema_identifier,
)


@pytest.mark.parametrize(
    "ok",
    ["genereview", "genereview_staging", "_x", "a1_b2", "A", "a" * 63],
)
def test_valid_identifiers_pass(ok: str) -> None:
    assert validate_schema_identifier(ok) == ok


@pytest.mark.parametrize(
    "bad",
    [
        "public; DROP TABLE x",
        'public"; drop table x --',
        "genereview-staging",
        "1genereview",
        "",
        "a" * 64,
        "schema with space",
        "sch;ema",
        "genereview\ndrop",
        "genereview$",
    ],
)
def test_malicious_identifiers_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_schema_identifier(bad)


def test_quote_pg_identifier_validates_then_wraps() -> None:
    assert quote_pg_identifier("genereview") == '"genereview"'
    with pytest.raises(ValueError):
        quote_pg_identifier("public; DROP TABLE x")


def test_cli_db_migrate_rejects_malicious_schema() -> None:
    from typer.testing import CliRunner

    from genereview_link.cli import app

    result = CliRunner().invoke(app, ["db", "migrate", "--schema", "public; DROP TABLE x"])
    assert result.exit_code != 0
    # Rejected as a bad CLI parameter, never reaching create_pool / SQL.
    assert "DROP TABLE" not in (result.stdout or "")


async def test_apply_data_migrations_rejects_malicious_schema_before_sql() -> None:
    # A MagicMock pool whose acquire() blows up proves no connection is ever
    # taken: validation must fail closed before any SQL touches the database.
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire a connection"))
    with pytest.raises(ValueError):
        await migrate.apply_data_migrations(pool, schema="public; DROP TABLE x")
    pool.acquire.assert_not_called()
