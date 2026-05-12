"""Tests for corpus COPY row plumbing."""

from __future__ import annotations

import pytest

from genereview_link.corpus.parallel import copy_passages
from genereview_link.corpus.records import PassageRecord


class _FakeConnection:
    def __init__(self) -> None:
        self.table: str | None = None
        self.records: list[tuple[object, ...]] | None = None
        self.columns: tuple[str, ...] | None = None

    async def copy_records_to_table(
        self,
        table: str,
        *,
        records: list[tuple[object, ...]],
        columns: tuple[str, ...],
    ) -> None:
        self.table = table
        self.records = records
        self.columns = columns


@pytest.mark.asyncio
async def test_copy_passages_includes_passage_role_column_and_value() -> None:
    conn = _FakeConnection()
    passage = PassageRecord(
        nbk_id="NBK_COPY",
        passage_id="NBK_COPY:0001",
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="Evidence passage.",
        char_count=17,
        token_estimate=2,
        passage_role="evidence",
    )

    await copy_passages(conn, [passage], corpus_version="v-test")  # type: ignore[arg-type]

    assert conn.table == "genereview_passages"
    assert conn.columns is not None
    assert conn.records is not None
    role_index = conn.columns.index("passage_role")
    assert conn.records[0][role_index] == "evidence"
