"""GeneReviewRepository.get_chapter_metadata integration tests."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.models.sections import SECTION_NAMES, SYSTEMATICALLY_UNSCRAPED_SECTIONS
from genereview_link.retrieval.repository import GeneReviewRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Seed: one chapter with passages in 'summary' (2) and 'diagnosis' (1).
# 'management' and all other canonical sections have zero passages.
_SEED_SQL_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKMETA', 'TG', 'TestGene Overview', 42,
        ARRAY['TG1', 'TG2'], ARRAY['600001']::text[], ARRAY['A. Author']::text[],
        'NBKMETA.xml', '2026-01-01', DATE '2025-06-15')
"""

_SEED_SQL_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version)
values
    ('NBKMETA', 'NBKMETA:0001', 'summary',   'Summary',   1, 0, 'Summary p0',   'h0', 10, 2, '2026-01-01'),
    ('NBKMETA', 'NBKMETA:0002', 'summary',   'Summary',   1, 1, 'Summary p1',   'h1', 10, 2, '2026-01-01'),
    ('NBKMETA', 'NBKMETA:0003', 'diagnosis', 'Diagnosis', 1, 2, 'Diagnosis p0', 'h2', 11, 2, '2026-01-01')
"""


async def _seed(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.genereview_corpus_version "
            "(version, file_list_etag, tarball_sha256, tarball_size_bytes, "
            " ingest_started_at, ingest_status, is_active) "
            "values ('2026-01-01','etag','sha',0,now(),'completed',true)"
        )
        await conn.execute(_SEED_SQL_CHAPTER)
        await conn.execute(_SEED_SQL_PASSAGES)


async def test_get_chapter_metadata_unknown_returns_none(pool: asyncpg.Pool) -> None:
    """Unknown nbk_id returns None."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBK0000000")
    assert meta is None


async def test_get_chapter_metadata_title_and_gene_symbols(pool: asyncpg.Pool) -> None:
    """Title and gene_symbols are returned correctly."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None
    assert meta.title == "TestGene Overview"
    assert meta.gene_symbols == ("TG1", "TG2")


async def test_get_chapter_metadata_last_updated(pool: asyncpg.Pool) -> None:
    """last_updated_date is returned as a date object."""
    from datetime import date

    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None
    assert meta.chapter_last_updated == date(2025, 6, 15)


async def test_get_chapter_metadata_ingested_at(pool: asyncpg.Pool) -> None:
    """ingested_at is projected as chapter_ingested_at."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None
    assert meta.chapter_ingested_at is not None


async def test_get_chapter_metadata_all_canonical_sections_present(pool: asyncpg.Pool) -> None:
    """All canonical sections are present in the result (including zero-count ones)."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None
    section_names = [s.section for s in meta.sections]
    assert list(section_names) == list(SECTION_NAMES)


async def test_get_chapter_metadata_passage_counts(pool: asyncpg.Pool) -> None:
    """Sections with passages show correct counts; others show zero."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None

    counts = {s.section: s.passage_count for s in meta.sections}

    assert counts["summary"] == 2
    assert counts["diagnosis"] == 1
    # All other canonical sections must be zero (no passages seeded for them)
    for name in SECTION_NAMES:
        if name not in ("summary", "diagnosis"):
            assert counts[name] == 0, f"expected 0 for {name}, got {counts[name]}"


async def test_get_chapter_metadata_table_count_is_zero(pool: asyncpg.Pool) -> None:
    """table_count is 0 for a chapter that has only narrative passages."""
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKMETA")
    assert meta is not None
    assert meta.table_count == 0


# Seed SQL for a chapter that has table-type passages
_SEED_SQL_TABLE_CHAPTER = """
insert into genereview.genereview_chapters
    (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
     authors, nxml_relpath, corpus_version, last_updated_date)
values ('NBKTBL', 'TBTG', 'TableGene Overview', null,
        ARRAY['TBTG1'], ARRAY['700001']::text[], ARRAY['B. Author']::text[],
        'NBKTBL.xml', '2026-01-01', DATE '2025-07-01')
"""

_SEED_SQL_TABLE_PASSAGES = """
insert into genereview.genereview_passages
    (nbk_id, passage_id, chapter_section, heading_path,
     section_level, chunk_index, text, text_hash,
     char_count, token_estimate, corpus_version,
     passage_type, table_id, table_data)
values
    ('NBKTBL', 'NBKTBL:0001', 'diagnosis', 'Diagnosis', 1, 0, 'Narrative p0', 'th0', 12, 2, '2026-01-01',
     'narrative', null, null),
    ('NBKTBL', 'NBKTBL:0002', 'diagnosis', 'Diagnosis', 1, 1, 'Table p0', 'th1', 8, 2, '2026-01-01',
     'table', 'T1', '{"caption":"Cap1","header":["A","B"],"rows":[["1","2"]]}'),
    ('NBKTBL', 'NBKTBL:0003', 'diagnosis', 'Diagnosis', 1, 2, 'Table p1', 'th2', 8, 2, '2026-01-01',
     'table', 'T2', '{"caption":"Cap2","header":["X","Y"],"rows":[["a","b"]]}')
"""


async def test_get_chapter_metadata_table_count_populated(pool: asyncpg.Pool) -> None:
    """table_count reflects the real number of table passages for a chapter."""
    await _seed(pool)
    async with pool.acquire() as conn:
        await conn.execute(_SEED_SQL_TABLE_CHAPTER)
        await conn.execute(_SEED_SQL_TABLE_PASSAGES)
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKTBL")
    assert meta is not None
    assert meta.table_count == 2


async def test_get_chapter_metadata_gene_symbols_null_safe(pool: asyncpg.Pool) -> None:
    """gene_symbols is always a tuple, even when the DB column holds an empty array."""
    await _seed(pool)
    # Insert a chapter with an empty gene_symbols array
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into genereview.genereview_chapters
                (nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                 authors, nxml_relpath, corpus_version, last_updated_date)
            values ('NBKEMPTY', 'EG', 'Empty Genes Chapter', null,
                    ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[],
                    'NBKEMPTY.xml', '2026-01-01', null)
            """
        )
    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKEMPTY")
    assert meta is not None
    assert isinstance(meta.gene_symbols, tuple)
    assert meta.gene_symbols == ()
    assert meta.chapter_last_updated is None


@pytest.mark.integration
async def test_get_chapter_metadata_section_total_char_count(pool: asyncpg.Pool) -> None:
    """Each section's total_char_count equals SUM(char_count) over its passages."""
    await _seed(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into genereview.genereview_chapters "
            "(nbk_id, short_name, title, gene_symbols, omim_ids, nxml_relpath, corpus_version) "
            "values ('NBKCHRCT', 'chrct', 'CharCount Test', '{}', '{}', 'NBKCHRCT.xml', '2026-01-01') "
            "on conflict do nothing"
        )
        # Two narrative passages in 'management', one in 'diagnosis'.
        for pid, section, text in [
            ("NBKCHRCT:0000", "management", "abc" * 100),  # 300 chars
            ("NBKCHRCT:0001", "management", "xyz" * 50),  # 150 chars
            ("NBKCHRCT:0002", "diagnosis", "qrs" * 200),  # 600 chars
        ]:
            await conn.execute(
                "insert into genereview.genereview_passages "
                "(nbk_id, passage_id, chapter_section, "
                "section_level, chunk_index, text, text_hash, char_count, token_estimate, "
                "corpus_version, passage_type) "
                "values ('NBKCHRCT',$1,$2,1,$3,$4,'fake_hash_' || $1,length($4),0,'2026-01-01','narrative') "
                "on conflict do nothing",
                pid,
                section,
                int(pid.split(":")[1]),
                text,
            )

    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKCHRCT")
    assert meta is not None
    by_section = {s.section: s for s in meta.sections}
    assert by_section["management"].total_char_count == 450
    assert by_section["diagnosis"].total_char_count == 600
    # Sections with no passages get 0.
    assert by_section["summary"].total_char_count == 0


@pytest.mark.integration
async def test_get_chapter_metadata_unscraped_section_emits_note(pool: asyncpg.Pool) -> None:
    """A canonical section in SYSTEMATICALLY_UNSCRAPED_SECTIONS with zero passages
    gets a non-empty note explaining the absence."""
    await _seed(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into genereview.genereview_chapters "
            "(nbk_id, short_name, title, gene_symbols, omim_ids, nxml_relpath, corpus_version) "
            "values ('NBKNOTES', 'notes', 'Notes Test', '{}', '{}', 'NBKNOTES.xml', '2026-01-01') "
            "on conflict do nothing"
        )
        # No 'summary' rows. Add a single narrative passage in another section.
        await conn.execute(
            "insert into genereview.genereview_passages "
            "(nbk_id, passage_id, chapter_section, "
            "section_level, chunk_index, text, text_hash, char_count, token_estimate, "
            "corpus_version, passage_type) "
            "values ('NBKNOTES','NBKNOTES:0000','diagnosis',1,0,'dx text','fake_hash',7,0,'2026-01-01','narrative') "
            "on conflict do nothing"
        )

    repo = GeneReviewRepository(pool)
    meta = await repo.get_chapter_metadata("NBKNOTES")
    assert meta is not None

    summary = next(s for s in meta.sections if s.section == "summary")
    assert summary.passage_count == 0
    assert summary.note is not None
    assert "summary" in summary.note.lower()
    assert "https://www.ncbi.nlm.nih.gov/books/NBKNOTES" in summary.note

    # A non-unscraped zero-count section gets no note.
    resources = next(s for s in meta.sections if s.section == "resources")
    assert resources.passage_count == 0
    assert resources.note is None

    # A section with passages (diagnosis) also gets no note.
    diagnosis = next(s for s in meta.sections if s.section == "diagnosis")
    assert diagnosis.passage_count == 1
    assert diagnosis.note is None

    # Sanity-check: 'summary' is indeed in SYSTEMATICALLY_UNSCRAPED_SECTIONS.
    assert "summary" in SYSTEMATICALLY_UNSCRAPED_SECTIONS
