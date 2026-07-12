"""Strict PostgreSQL identifier validation for dynamically-composed SQL.

Schema names reach dynamic SQL as double-quoted identifiers (``create schema``,
``set search_path``, ``create index on "<schema>"``). asyncpg cannot bind an
identifier as a parameter, so the value MUST be validated against a strict
grammar before it is interpolated. This is the ``sql.Identifier`` equivalent:
reject anything outside a conservative unquoted-identifier grammar, then wrap in
double quotes.
"""

from __future__ import annotations

import re

# PostgreSQL truncates identifiers at 63 bytes (NAMEDATALEN - 1). Restrict to
# the ASCII unquoted-identifier grammar: first char a letter or underscore, then
# letters/digits/underscores. This deliberately forbids quotes, semicolons,
# whitespace, and every other SQL metacharacter.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def validate_schema_identifier(schema: str) -> str:
    """Return *schema* unchanged iff it is a safe PostgreSQL identifier.

    Raises ``ValueError`` (fail-closed, before any SQL executes) otherwise.
    """
    if not isinstance(schema, str) or not _IDENTIFIER_RE.match(schema):
        # Do not echo the raw value into the message beyond a bounded, quoted
        # excerpt so hostile input cannot pivot through logs.
        raise ValueError(f"invalid schema identifier: {schema!r:.80}")
    return schema


def quote_pg_identifier(name: str) -> str:
    """Validate *name* then return it as a double-quoted SQL identifier."""
    return f'"{validate_schema_identifier(name)}"'
