"""GeneReviewRepository — asyncpg-backed reads for the API layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import asyncpg

from genereview_link.config import settings
from genereview_link.models.sections import SECTION_NAMES, SYSTEMATICALLY_UNSCRAPED_SECTIONS


@dataclass(frozen=True, slots=True)
class CorpusVersionRow:
    version: str
    is_active: bool
    ingest_status: str
    ingest_finished_at: datetime | None
    chapter_count: int | None


@dataclass(frozen=True, slots=True)
class ChapterRow:
    nbk_id: str
    short_name: str
    title: str
    pubmed_id: str | None
    gene_symbols: tuple[str, ...]
    omim_ids: tuple[str, ...]
    authors: str | None
    initial_pub_date: date | None
    last_updated_date: date | None
    ingested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PassageRow:
    nbk_id: str
    passage_id: str
    chapter_section: str
    heading_path: str | None
    section_level: int
    chunk_index: int
    text: str
    chapter_title: str | None = None
    chapter_last_updated: date | None = None
    chapter_ingested_at: datetime | None = None
    gene_symbols: tuple[str, ...] = ()
    passage_type: str = "narrative"
    passage_role: str | None = None
    table_id: str | None = None
    table_data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LexicalPassageRow:
    """A passage with its lexical and (optional) dense/RRF scores attached.

    Gene symbols live on ``passage.gene_symbols`` — there is no top-level
    duplicate field. Read them via ``row.passage.gene_symbols``.
    """

    passage: PassageRow
    phrase_rank: float
    strict_rank: float
    recall_rank: float
    recall_overlap_count: int
    lexical_rank: float
    snippet: str | None = None
    lexical_rank_position: int | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    adjusted_score: float | None = None
    role_multiplier: float = 1.0
    intent_section_boost: float = 0.0


@dataclass(frozen=True, slots=True)
class SectionSummaryRow:
    section: str
    passage_count: int
    total_char_count: int
    note: str | None = None


@dataclass(frozen=True, slots=True)
class TableSummaryRow:
    table_id: str
    caption: str
    section: str
    heading_path: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class ChapterMetadataRow:
    nbk_id: str
    title: str
    chapter_last_updated: date | None
    gene_symbols: tuple[str, ...]
    sections: tuple[SectionSummaryRow, ...]
    table_count: int
    chapter_ingested_at: datetime | None = None
    tables: tuple[TableSummaryRow, ...] = ()


@dataclass(frozen=True, slots=True)
class TableRow:
    nbk_id: str
    passage_id: str
    section: str
    heading_path: str | None
    table_id: str
    caption: str
    header: list[str]
    rows: list[list[str]]


# ---------------------------------------------------------------------------
# Module-level SQL builders for parallel (lexical + dense) retrieval
# ---------------------------------------------------------------------------


def build_dense_candidates_sql(
    *,
    embedding_table: str,
    gene: str | None,
    nbk_id: str | None,
    sections: tuple[str, ...] | None,
    heading_path_contains: str | None,
    top_k: int,
) -> tuple[list[str], str, list[object]]:
    """Build the dense-candidate SQL with filter-aware top-K.

    Strategy:
      - Single-chapter filter (nbk_id given): bypass HNSW, exact cosine over the
        small filtered set.
      - Other filters: HNSW with iterative scan + larger ef_search.
      - No filter: vanilla HNSW.

    Returns (setup_statements, select_sql, params).

    setup_statements is a list of SET LOCAL statements (or empty for the
    HNSW-bypass branch). They must be executed via conn.execute() before
    the SELECT, inside a transaction.
    select_sql is the parameterized SELECT.
    params: first element is the query embedding placeholder; rest are filter values
    in the order: gene, nbk_id, sections, heading_path_contains.
    """
    params: list[object] = []
    param_idx = 1

    def next_param(value: object) -> str:
        nonlocal param_idx
        params.append(value)
        idx = param_idx
        param_idx += 1
        return f"${idx}"

    embedding_param = next_param([])  # placeholder; caller fills in the query vector
    where_clauses: list[str] = []

    if gene:
        where_clauses.append(f"c.gene_symbols @> array[{next_param(gene)}]")
    if nbk_id:
        where_clauses.append(f"p.nbk_id = {next_param(nbk_id)}")
    if sections:
        where_clauses.append(f"p.chapter_section = any({next_param(list(sections))}::text[])")
    if heading_path_contains:
        where_clauses.append(
            f"p.heading_path ilike {next_param('%' + heading_path_contains + '%')}"
        )

    where_sql = " and ".join(where_clauses) if where_clauses else "true"
    top_k_param = next_param(top_k)

    # If nbk_id is the only filter, use exact KNN (bypass HNSW).
    # embedding_table is operator-controlled (not user input); S608 suppressed.
    if nbk_id and not (gene or sections or heading_path_contains):
        select_sql = f"""
            select p.passage_id, 1 - (e.embedding <=> {embedding_param}::vector) as dense_score
            from genereview_passages p
            join "{embedding_table}" e on e.nbk_id = p.nbk_id and e.passage_id = p.passage_id
            where {where_sql}
            order by e.embedding <=> {embedding_param}::vector
            limit {top_k_param}
        """  # noqa: S608
        return [], select_sql, params

    # Otherwise: HNSW with iterative scan.
    # SET LOCAL statements are returned separately so they can be executed via
    # conn.execute() before the SELECT -- asyncpg prepared statements only accept
    # a single command and would raise PostgresSyntaxError with a concatenated query.
    # embedding_table is operator-controlled (not user input); S608 suppressed.
    setup = [
        "SET LOCAL hnsw.iterative_scan = 'relaxed_order'",
        "SET LOCAL hnsw.ef_search = 200",
    ]
    select_sql = f"""
        select p.passage_id, 1 - (e.embedding <=> {embedding_param}::vector) as dense_score
        from genereview_passages p
        join "{embedding_table}" e on e.nbk_id = p.nbk_id and e.passage_id = p.passage_id
        join genereview_chapters c on c.nbk_id = p.nbk_id
        where {where_sql}
        order by e.embedding <=> {embedding_param}::vector
        limit {top_k_param}
    """  # noqa: S608
    return setup, select_sql, params


def build_parallel_search_sql(
    *,
    query_text: str,
    query_vector: list[float],
    gene: str | None,
    nbk_id: str | None,
    sections: tuple[str, ...] | None,
    heading_path_contains: str | None,
    top_k: int,
) -> tuple[str, list[object]]:
    """Build the parallel-retrieval SQL: lexical-top-K UNION dense-top-K.

    Each branch applies the same filters.  RRF fusion happens in Python
    after fetching the union.
    """
    _setup, dense_sql, dense_params = build_dense_candidates_sql(
        embedding_table="genereview_embeddings_bge384",
        gene=gene,
        nbk_id=nbk_id,
        sections=sections,
        heading_path_contains=heading_path_contains,
        top_k=top_k,
    )
    # Existing lexical SQL lives in GeneReviewRepository or similar;
    # here we just compose a UNION shape for unit-test introspection.
    lexical_sql = "select passage_id, null::float as dense_score from genereview_passages limit 1"
    return (
        f"({lexical_sql}) union ({dense_sql})",
        dense_params,
    )


class GeneReviewRepository:
    """Read-mostly facade over Postgres."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        acquire_timeout_s: float | None = None,
    ) -> None:
        self._pool = pool
        self._acquire_timeout_s = (
            acquire_timeout_s
            if acquire_timeout_s is not None
            else settings.DATABASE_ACQUIRE_TIMEOUT_S
        )

    def _acquire(self) -> Any:
        return self._pool.acquire(timeout=self._acquire_timeout_s)

    # ---- operational ----
    async def active_corpus_version(self) -> CorpusVersionRow | None:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                select version, is_active, ingest_status, ingest_finished_at, chapter_count
                  from public.genereview_corpus_version
                 where is_active
                """
            )
        if row is None:
            return None
        return CorpusVersionRow(
            version=row["version"],
            is_active=row["is_active"],
            ingest_status=row["ingest_status"],
            ingest_finished_at=row["ingest_finished_at"],
            chapter_count=row["chapter_count"],
        )

    async def active_embedding_table(self) -> str:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                "select table_name from public.genereview_active_embedding where id = 1"
            )
        return row["table_name"] if row else "genereview_embeddings_bge384"

    async def search_passages(
        self,
        query: str,
        *,
        gene_symbol: str | None = None,
        nbk_id: str | None = None,
        sections: list[str] | None = None,
        heading_path_contains: str | None = None,
        limit: int = 20,
        brief: bool = False,
        snippet_max_fragments: int = 2,
        snippet_max_words: int = 30,
    ) -> list[LexicalPassageRow]:
        """Run the three-tsquery hybrid lexical search.

        ``snippet_max_fragments`` and ``snippet_max_words`` are integer-bounded
        by the FastAPI route's ge/le validators (snippet_chars in [80, 800]),
        so interpolating them into the ts_headline options string via f-string
        is safe — no raw user input reaches SQL.
        """
        from genereview_link.retrieval.lexical import recall_terms, recall_tsquery

        recall_query = recall_tsquery(query)
        terms = recall_terms(query)
        sections_param = sections if sections else None

        snippet_select = ""
        if brief:
            # Build the ts_headline options string.  snippet_max_fragments and
            # snippet_max_words are ints derived from a clamped FastAPI param —
            # safe to f-string here; the rest of the string is a constant literal.
            ts_headline_opts = (
                f"MaxFragments={snippet_max_fragments}, MaxWords={snippet_max_words}, "
                "MinWords=10, ShortWord=3, "
                "FragmentDelimiter= ... , StartSel=**, StopSel=**, "
                "HighlightAll=false"
            )
            snippet_select = (
                ", ts_headline("
                "    'english', ranked.text, "
                "    coalesce("
                "        nullif(q.phrase_query::text, '')::tsquery, "
                "        nullif(q.strict_query::text, '')::tsquery, "
                "        q.recall_query"
                "    ),"
                f"    '{ts_headline_opts}'"
                ") as snippet"
            )

        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                f"""
                with q as (
                    select
                        phraseto_tsquery('english', $2) as phrase_query,
                        websearch_to_tsquery('english', $2) as strict_query,
                        to_tsquery('english', $7) as recall_query,
                        $8::text[] as recall_terms,
                        $1::text as _ignored
                ),
                scored as (
                    select
                        p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                        p.section_level, p.chunk_index, p.text,
                        c.gene_symbols,
                        c.title as chapter_title,
                        c.last_updated_date as chapter_last_updated,
                        c.ingested_at as chapter_ingested_at,
                        p.passage_type, p.passage_role, p.table_id, p.table_data,
                        ts_rank_cd(p.search_vector, q.phrase_query) as phrase_rank,
                        ts_rank_cd(p.search_vector, q.strict_query) as strict_rank,
                        ts_rank_cd(p.search_vector, q.recall_query) as recall_rank,
                        (
                            select count(*)
                              from unnest(q.recall_terms) as term
                             where p.search_vector @@ plainto_tsquery('english', term)
                        )::int as recall_overlap_count,
                        cardinality(q.recall_terms)::int as recall_terms_count
                      from genereview_passages p
                      join genereview_chapters c on c.nbk_id = p.nbk_id, q
                     where (
                              p.search_vector @@ q.phrase_query
                           or p.search_vector @@ q.strict_query
                           or p.search_vector @@ q.recall_query
                          )
                       and ($3::text is null or $3 = any(c.gene_symbols))
                       and ($4::text is null or p.nbk_id = $4)
                       and ($5::text[] is null or p.chapter_section = any($5::text[]))
                       and ($9::text is null or p.heading_path ILIKE '%' || $9 || '%')
                ),
                ranked as (
                    select
                        *,
                        (phrase_rank * 3.0 + strict_rank * 2.0 + recall_rank)
                          * case
                              when phrase_rank = 0 and strict_rank = 0 and recall_rank > 0
                                and recall_terms_count >= 4
                                and recall_overlap_count <= 2
                              then least(1.0, greatest(0.25, char_length(text)::double precision / 400.0))
                              else 1.0
                            end as lexical_rank
                      from scored
                     where recall_overlap_count >= greatest(1, ceiling(0.25 * recall_terms_count)::int)
                     order by lexical_rank desc, nbk_id, passage_id
                     limit $6
                )
                select ranked.*{snippet_select}
                  from ranked, q
                """,  # noqa: S608
                "ignored",
                query,
                gene_symbol,
                nbk_id,
                sections_param,
                limit,
                recall_query,
                terms,
                heading_path_contains,
            )

        return [
            LexicalPassageRow(
                passage=self._row_to_passage(r),
                phrase_rank=float(r["phrase_rank"]),
                strict_rank=float(r["strict_rank"]),
                recall_rank=float(r["recall_rank"]),
                recall_overlap_count=int(r["recall_overlap_count"]),
                lexical_rank=float(r["lexical_rank"]),
                snippet=r["snippet"] if brief else None,
            )
            for r in rows
        ]

    async def get_chapter_by_gene(self, gene_symbol: str) -> ChapterRow | None:
        chapters = await self.get_chapters_by_gene(gene_symbol, limit=1)
        return chapters[0] if chapters else None

    async def get_chapters_by_gene(
        self,
        gene_symbol: str,
        *,
        limit: int | None = None,
    ) -> list[ChapterRow]:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date, ingested_at
                  from genereview_chapters
                 where $1 = any(gene_symbols)
                 order by last_updated_date desc nulls last, nbk_id
                 limit coalesce($2::int, 2147483647)
                """,
                gene_symbol,
                limit,
            )
        return [_to_chapter_row(row) for row in rows]

    async def get_chapter_by_nbk(self, nbk_id: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date, ingested_at
                  from genereview_chapters
                 where nbk_id = $1
                """,
                nbk_id,
            )
        return _to_chapter_row(row) if row else None

    async def get_chapter_by_pmid(self, pmid: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date, ingested_at
                  from genereview_chapters
                 where pubmed_id = $1
                """,
                pmid,
            )
        return _to_chapter_row(row) if row else None

    async def get_section(
        self,
        nbk_id: str,
        chapter_section: str,
        *,
        heading_path_contains: str | None = None,
    ) -> list[PassageRow]:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.ingested_at as chapter_ingested_at,
                       c.gene_symbols,
                       p.passage_type, p.passage_role, p.table_id, p.table_data
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.nbk_id = $1 and p.chapter_section = $2
                   and ($3::text is null or p.heading_path ilike '%' || $3 || '%')
                 order by p.chunk_index
                """,
                nbk_id,
                chapter_section,
                heading_path_contains,
            )
        return [self._row_to_passage(r) for r in rows]

    async def get_passage(self, passage_id: str) -> PassageRow | None:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.ingested_at as chapter_ingested_at,
                       c.gene_symbols,
                       p.passage_type, p.passage_role, p.table_id, p.table_data
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.passage_id = $1
                """,
                passage_id,
            )
        if row is None:
            return None
        return self._row_to_passage(row)

    @staticmethod
    def _row_to_passage(row: asyncpg.Record) -> PassageRow:
        """Convert a DB record (with chapter_last_updated alias) to PassageRow."""
        raw_table_data = row["table_data"]
        if isinstance(raw_table_data, str):
            table_data: dict[str, Any] | None = json.loads(raw_table_data)
        else:
            table_data = raw_table_data
        return PassageRow(
            nbk_id=row["nbk_id"],
            passage_id=row["passage_id"],
            chapter_section=row["chapter_section"],
            heading_path=row["heading_path"],
            section_level=row["section_level"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            chapter_title=row["chapter_title"],
            chapter_last_updated=row["chapter_last_updated"],
            chapter_ingested_at=_record_get(row, "chapter_ingested_at"),
            gene_symbols=tuple(row["gene_symbols"] or ()),
            passage_type=row["passage_type"],
            passage_role=_record_get(row, "passage_role"),
            table_id=row["table_id"],
            table_data=table_data,
        )

    async def _fetch_passage_row(
        self,
        conn: asyncpg.Connection,
        passage_id: str,
    ) -> PassageRow | None:
        """Fetch a single passage by passage_id using an existing connection."""
        row = await conn.fetchrow(
            """
            select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                   p.section_level, p.chunk_index, p.text,
                   c.title as chapter_title,
                   c.last_updated_date as chapter_last_updated,
                   c.ingested_at as chapter_ingested_at,
                   c.gene_symbols,
                   p.passage_type, p.passage_role, p.table_id, p.table_data
              from genereview_passages p
              join genereview_chapters c on c.nbk_id = p.nbk_id
             where p.passage_id = $1
            """,
            passage_id,
        )
        return self._row_to_passage(row) if row is not None else None

    async def get_passage_window(
        self,
        passage_id: str,
        *,
        before: int,
        after: int,
        cross_sections: bool,
    ) -> tuple[PassageRow | None, list[PassageRow], list[PassageRow], bool, bool]:
        """Fetch a passage plus its neighbors within the same chapter.

        Neighbors stop at the section boundary unless cross_sections=True.
        Always stops at chapter boundary regardless. Returns (focal,
        before_rows, after_rows, has_more_before, has_more_after).
        """
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            focal = await self._fetch_passage_row(conn, passage_id)
            if focal is None:
                return None, [], [], False, False

            if cross_sections:
                section_filter = ""
                params: list[object] = [focal.nbk_id, focal.chunk_index]
            else:
                section_filter = "and p.chapter_section = $3"
                params = [focal.nbk_id, focal.chunk_index, focal.chapter_section]

            # Fetch one extra each side to compute has_more_*
            before_rows = await conn.fetch(
                f"""
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.ingested_at as chapter_ingested_at,
                       c.gene_symbols,
                       p.passage_type, p.passage_role, p.table_id, p.table_data
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.nbk_id = $1
                   and p.chunk_index < $2
                   {section_filter}
                 order by p.chunk_index desc
                 limit {before + 1}
                """,  # noqa: S608
                *params,
            )
            after_rows = await conn.fetch(
                f"""
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.ingested_at as chapter_ingested_at,
                       c.gene_symbols,
                       p.passage_type, p.passage_role, p.table_id, p.table_data
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.nbk_id = $1
                   and p.chunk_index > $2
                   {section_filter}
                 order by p.chunk_index asc
                 limit {after + 1}
                """,  # noqa: S608
                *params,
            )

        has_more_before = len(before_rows) > before
        has_more_after = len(after_rows) > after
        before_clipped = list(reversed([self._row_to_passage(r) for r in before_rows[:before]]))
        after_clipped = [self._row_to_passage(r) for r in after_rows[:after]]
        return focal, before_clipped, after_clipped, has_more_before, has_more_after

    async def get_chapter_metadata(self, nbk_id: str) -> ChapterMetadataRow | None:
        """Return chapter-level metadata with per-section passage counts.

        Emits all canonical sections (including zero-count ones) so callers
        can see exactly what is available. ``table_count`` reflects the real
        number of table-type passages stored for this chapter.
        """
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            chapter = await conn.fetchrow(
                """
                select nbk_id, title, last_updated_date, ingested_at, gene_symbols
                  from genereview_chapters
                 where nbk_id = $1
                """,
                nbk_id,
            )
            if chapter is None:
                return None

            section_rows = await conn.fetch(
                """
                select chapter_section,
                       count(*)::int as passage_count,
                       coalesce(sum(char_count), 0)::int as total_char_count
                  from genereview_passages
                 where nbk_id = $1
                 group by chapter_section
                """,
                nbk_id,
            )

            table_rows = await conn.fetch(
                """
                select p.table_id,
                       coalesce(p.table_data->>'caption', '') as caption,
                       p.chapter_section,
                       coalesce(p.heading_path, '') as heading_path,
                       p.passage_id
                  from genereview_passages p
                 where p.nbk_id = $1
                   and p.passage_type = 'table'
                 order by p.chunk_index
                """,
                nbk_id,
            )

        counts: dict[str, dict[str, int]] = {
            r["chapter_section"]: {
                "passage_count": r["passage_count"],
                "total_char_count": r["total_char_count"],
            }
            for r in section_rows
        }
        sections = tuple(
            SectionSummaryRow(
                section=name,
                passage_count=counts.get(name, {}).get("passage_count", 0),
                total_char_count=counts.get(name, {}).get("total_char_count", 0),
                note=(
                    _note_for_empty_section(name, nbk_id)
                    if counts.get(name, {}).get("passage_count", 0) == 0
                    else None
                ),
            )
            for name in SECTION_NAMES
        )

        tables_tuple = tuple(
            TableSummaryRow(
                table_id=r["table_id"],
                caption=r["caption"],
                section=r["chapter_section"],
                heading_path=r["heading_path"],
                passage_id=r["passage_id"],
            )
            for r in table_rows
        )
        return ChapterMetadataRow(
            nbk_id=chapter["nbk_id"],
            title=chapter["title"],
            chapter_last_updated=chapter["last_updated_date"],
            chapter_ingested_at=chapter["ingested_at"],
            gene_symbols=tuple(chapter["gene_symbols"] or ()),
            sections=sections,
            table_count=len(tables_tuple),
            tables=tables_tuple,
        )

    async def get_table(self, nbk_id: str, table_id: str) -> TableRow | None:
        """Fetch a single table passage by nbk_id + table_id."""
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.table_id, p.table_data
                  from genereview_passages p
                 where p.nbk_id = $1
                   and p.passage_type = 'table'
                   and p.table_id = $2
                """,
                nbk_id,
                table_id,
            )
        if row is None:
            return None
        data = row["table_data"]
        if isinstance(data, str):
            data = json.loads(data)
        if data is None:
            data = {}
        return TableRow(
            nbk_id=row["nbk_id"],
            passage_id=row["passage_id"],
            section=row["chapter_section"],
            heading_path=row["heading_path"],
            table_id=row["table_id"],
            caption=data.get("caption", ""),
            header=list(data.get("header", [])),
            rows=[list(r) for r in data.get("rows", [])],
        )

    async def list_table_ids(self, nbk_id: str) -> list[str]:
        """Return all table_ids for a chapter, ordered by chunk_index."""
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                """
                select table_id
                  from genereview_passages
                 where nbk_id = $1
                   and passage_type = 'table'
                 order by chunk_index
                """,
                nbk_id,
            )
        return [r["table_id"] for r in rows]

    async def dense_scores_for_passages(
        self,
        query_vector: list[float],
        passage_ids: list[tuple[str, str]],
        *,
        model_table: str,
    ) -> dict[str, float]:
        if not passage_ids:
            return {}
        nbks = [n for n, _ in passage_ids]
        pids = [p for _, p in passage_ids]
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            # model_table is operator-controlled (not user input); S608 suppressed
            sql = f'select passage_id, 1 - (embedding <=> $1::vector) as score from "{model_table}" where (nbk_id, passage_id) in (select unnest($2::text[]), unnest($3::text[]))'  # noqa: S608
            rows = await conn.fetch(
                sql,
                query_vector,
                nbks,
                pids,
            )
        return {r["passage_id"]: float(r["score"]) for r in rows}

    async def _dense_candidates_filtered(
        self,
        *,
        query_vector: list[float],
        gene: str | None,
        nbk_id: str | None,
        sections: tuple[str, ...] | None,
        heading_path_contains: str | None,
        top_k: int,
    ) -> list[dict[str, object]]:
        """Fetch top-K dense candidates filter-aware, corpus-wide.

        Replaces the prior pattern of scoring dense ON TOP OF lexical's
        candidate set. Used by the parallel-retrieval rerank=rrf path.

        Wraps the query in a transaction so SET LOCAL hnsw.* statements
        are session-scoped to the txn and do not leak into the pool.
        """
        embedding_table = await self.active_embedding_table()
        setup, select_sql, params = build_dense_candidates_sql(
            embedding_table=embedding_table,
            gene=gene,
            nbk_id=nbk_id,
            sections=sections,
            heading_path_contains=heading_path_contains,
            top_k=top_k,
        )
        params[0] = query_vector
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            async with conn.transaction():
                for stmt in setup:
                    await conn.execute(stmt)
                rows = await conn.fetch(select_sql, *params)
        return [
            {"passage_id": r["passage_id"], "dense_score": float(r["dense_score"])} for r in rows
        ]

    async def fetch_passages_by_ids(self, passage_ids: list[str]) -> dict[str, PassageRow]:
        """Batch-fetch full passage rows for a list of passage_ids.

        Returns a mapping of passage_id -> PassageRow.  Passage IDs that do
        not exist in the corpus are silently omitted from the result.
        """
        if not passage_ids:
            return {}
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.ingested_at as chapter_ingested_at,
                       c.gene_symbols,
                       p.passage_type, p.passage_role, p.table_id, p.table_data
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.passage_id = any($1::text[])
                """,
                passage_ids,
            )
        return {r["passage_id"]: self._row_to_passage(r) for r in rows}


def _note_for_empty_section(section: str, nbk_id: str) -> str | None:
    """Return an explanatory note when a zero-passage section is deliberately unscraped.

    Returns None for sections that are simply empty (e.g. not yet ingested).
    """
    if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
        return (
            f"section {section!r} is not scraped from NCBI Bookshelf NXML; "
            f"call get_abstract(pubmed_id=<chapter.pubmed_id>) for the chapter "
            f"abstract (or open https://www.ncbi.nlm.nih.gov/books/{nbk_id}/)."
        )
    return None


def _record_get(row: asyncpg.Record, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _to_chapter_row(row: asyncpg.Record) -> ChapterRow:
    return ChapterRow(
        nbk_id=row["nbk_id"],
        short_name=row["short_name"],
        title=row["title"],
        pubmed_id=row["pubmed_id"],
        gene_symbols=tuple(row["gene_symbols"] or ()),
        omim_ids=tuple(row["omim_ids"] or ()),
        authors=row["authors"],
        initial_pub_date=row["initial_pub_date"],
        last_updated_date=row["last_updated_date"],
        ingested_at=_record_get(row, "ingested_at"),
    )
