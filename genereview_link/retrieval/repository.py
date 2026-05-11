"""GeneReviewRepository — asyncpg-backed reads for the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import asyncpg

from genereview_link.config import settings


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
    gene_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LexicalPassageRow:
    """A passage with its lexical scores attached.

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
        limit: int = 20,
        brief: bool = False,
    ) -> list[LexicalPassageRow]:
        """Run the three-tsquery hybrid lexical search."""
        from genereview_link.retrieval.lexical import recall_terms, recall_tsquery

        recall_query = recall_tsquery(query)
        terms = recall_terms(query)
        sections_param = sections if sections else None

        snippet_select = ""
        if brief:
            snippet_select = (
                ", ts_headline("
                "    'english', ranked.text, "
                "    coalesce("
                "        nullif(q.phrase_query::text, '')::tsquery, "
                "        nullif(q.strict_query::text, '')::tsquery, "
                "        q.recall_query"
                "    ),"
                "    'MaxWords=60, MinWords=30, MaxFragments=2, "
                "FragmentDelimiter= ... , StartSel=**, StopSel=**, "
                "HighlightAll=false'"
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
                        $1::text as _ignored
                ),
                cand as (
                    select
                        p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                        p.section_level, p.chunk_index, p.text,
                        c.gene_symbols,
                        c.title as chapter_title,
                        c.last_updated_date as chapter_last_updated,
                        ts_rank_cd(p.search_vector, q.phrase_query) as phrase_rank,
                        ts_rank_cd(p.search_vector, q.strict_query) as strict_rank,
                        ts_rank_cd(p.search_vector, q.recall_query) as recall_rank,
                        (
                            select count(*)
                              from (
                                  select distinct token
                                    from regexp_split_to_table(lower(p.text), '[^a-zA-Z0-9]+') as token
                                   where length(token) >= 3
                              ) pt
                             where pt.token = any($8::text[])
                        ) as recall_overlap_count
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
                ),
                ranked as (
                    select
                        nbk_id, passage_id, chapter_section, heading_path,
                        section_level, chunk_index, text,
                        gene_symbols, chapter_title, chapter_last_updated,
                        phrase_rank, strict_rank, recall_rank, recall_overlap_count,
                        (phrase_rank * 3.0 + strict_rank * 2.0 + recall_rank)
                          * case
                              when phrase_rank = 0 and strict_rank = 0 and recall_rank > 0
                                and array_length(regexp_split_to_array($2, E'\\s+'), 1) >= 4
                                and recall_overlap_count <= 1
                              then least(1.0, greatest(0.25, char_length(text)::double precision / 400.0))
                              else 1.0
                            end as lexical_rank
                      from cand
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
            )

        return [
            LexicalPassageRow(
                passage=PassageRow(
                    nbk_id=r["nbk_id"],
                    passage_id=r["passage_id"],
                    chapter_section=r["chapter_section"],
                    heading_path=r["heading_path"],
                    section_level=r["section_level"],
                    chunk_index=r["chunk_index"],
                    text=r["text"],
                    chapter_title=r["chapter_title"],
                    chapter_last_updated=r["chapter_last_updated"],
                    gene_symbols=tuple(r["gene_symbols"] or ()),
                ),
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
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date
                  from genereview_chapters
                 where $1 = any(gene_symbols)
                 order by last_updated_date desc nulls last
                 limit 1
                """,
                gene_symbol,
            )
        return _to_chapter_row(row) if row else None

    async def get_chapter_by_nbk(self, nbk_id: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date
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
                       authors, initial_pub_date, last_updated_date
                  from genereview_chapters
                 where pubmed_id = $1
                """,
                pmid,
            )
        return _to_chapter_row(row) if row else None

    async def get_section(self, nbk_id: str, chapter_section: str) -> list[PassageRow]:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.gene_symbols
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.nbk_id = $1 and p.chapter_section = $2
                 order by p.chunk_index
                """,
                nbk_id,
                chapter_section,
            )
        return [
            PassageRow(
                nbk_id=r["nbk_id"],
                passage_id=r["passage_id"],
                chapter_section=r["chapter_section"],
                heading_path=r["heading_path"],
                section_level=r["section_level"],
                chunk_index=r["chunk_index"],
                text=r["text"],
                chapter_title=r["chapter_title"],
                chapter_last_updated=r["chapter_last_updated"],
                gene_symbols=tuple(r["gene_symbols"] or ()),
            )
            for r in rows
        ]

    async def get_passage(self, passage_id: str) -> PassageRow | None:
        async with self._acquire() as conn:
            await conn.execute("set search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                       p.section_level, p.chunk_index, p.text,
                       c.title as chapter_title,
                       c.last_updated_date as chapter_last_updated,
                       c.gene_symbols
                  from genereview_passages p
                  join genereview_chapters c on c.nbk_id = p.nbk_id
                 where p.passage_id = $1
                """,
                passage_id,
            )
        if row is None:
            return None
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
            gene_symbols=tuple(row["gene_symbols"] or ()),
        )

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
    )
