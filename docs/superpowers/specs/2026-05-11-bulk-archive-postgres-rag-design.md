# Bulk Archive Ingest + Postgres RAG Retrieval — Design Spec

- **Date:** 2026-05-11
- **Repo:** genereviews-link
- **References:**
  - `../pubtator-link` (lift retrieval + migration patterns)
  - `../phentrieve` (lift CI-baked bundle distribution pattern)
- **Status:** Approved by user, ready for plan generation

## Goal

Replace live NCBI HTML scraping as the primary data path with a periodic bulk
ingest of the canonical GeneReviews archive (`gene_NBK1116.tar.gz`) into a
Postgres + pgvector backend. Build a hybrid retrieval stack (lexical
`tsvector` + dense BGE-small embeddings + RRF fusion + section-priority
reranking) on top of the indexed corpus. Distribute the populated database
as a CI-built bundle attached to GitHub Releases; the runtime container
downloads and restores the bundle on first boot. Keep the existing
`EutilsClient` scraper as an opt-in `?fresh=true` fallback.

## Non-goals

- No multi-tenant features (saved searches, user annotations, audit trails)
  in v1. Schema does not preclude them.
- No cross-encoder reranker in v1 (e.g., `bge-reranker-v2-m3`). Pattern
  reserved for v1.1 if eval gap surfaces.
- No table / figure extraction from NXML in v1.
- No `<ref-list>` ingestion in v1 (Phase 7, v1.1).
- No federation with pubtator-link Postgres instance. genereview-link runs
  its own DB, own docker-compose, own release cycle.

## Decisions (locked in during brainstorm)

| Area | Decision |
| --- | --- |
| Primary data source | `gene_NBK1116.tar.gz` (BITS NXML) from `https://ftp.ncbi.nlm.nih.gov/pub/litarch/ca/84/` |
| Side-data source | `https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/` (NBK→gene, NBK→OMIM, NBK→title-shortname) |
| Live fallback | Existing `EutilsClient` scraper, gated behind `?fresh=true` query parameter |
| Database | Postgres 18 with pgvector 0.8.2, isolated per project |
| Embedding model | BGE-small-en-v1.5 (384d, MIT-ish license) |
| Lexical search | Three-tsquery pattern (`phraseto_tsquery` × 3.0 + `websearch_to_tsquery` × 2.0 + recall `to_tsquery` × 1.0) with weak-recall penalty |
| Vector index | HNSW with `m=16, ef_construction=200`, cosine ops |
| Fusion strategy | Reciprocal Rank Fusion (RRF), `k=60` |
| Reranker | RRF + section-priority + source-priority tiebreak (no cross-encoder in v1) |
| Chunking | 512-token windows with 50-token overlap, never crossing `<sec>` boundaries |
| API contract | Five existing routes preserved with additive `corpus_version` + `license` fields; three new routes in v1, one in v1.1 |
| MCP contract | Five existing tools preserved; two new tools in v1 (`search_passages`, `get_chapter_section`), one in v1.1 (`find_chapters_citing_pmid`); `/debug/ranking` excluded from MCP |
| Distribution | CI builds bundles, GitHub Releases hosts them, runtime container downloads + `pg_restore`s on first boot |
| Bundle format | `pg_dump -Fc` (custom format, parallel-restorable) + `manifest.json` + raw side-data, tarred + gzipped |
| Refresh model | Weekly GitHub Actions cron (Monday 06:00 UTC) + manual `workflow_dispatch` |
| Container ingest | Removed from runtime. Optional hourly check for newer releases; auto-pull off by default |
| Package layout | Decomposed by lifecycle: `corpus/`, `retrieval/`, `db/`, `ingest/`, `api/`, `services/`, `models/` |
| Embedding flexibility | One table per model (`genereview_embeddings_bge384`, etc.); `genereview_active_embedding` singleton points at the live one. No `CHECK (embedding_dim = N)` constraint. |
| Atomic corpus swap | Partial unique index `where is_active`; transaction flips active version |
| Parallelization (CI build) | `ProcessPoolExecutor` for NXML parse + chunk (N=min(cpu_count, 8)); pipelined encoder+writers for embeddings; asyncpg `copy_records_to_table` for bulk writes |
| Migration tool | Custom runner ported from pubtator-link (`db/migrate.py` + `schema_migrations` table); lexical-ordered `0001_*.sql`, `0002_*.sql`, ... |
| License compliance | UW copyright notice attached to every API response and every corpus snapshot row |

## Architecture

### Runtime topology

Single container, single Postgres service, single docker-compose:

```
┌────────────────────────────── docker-compose ──────────────────────────────┐
│                                                                            │
│  ┌─────────────────────────┐         ┌──────────────────────────────┐      │
│  │   genereview-link       │         │  postgres                    │      │
│  │   (single container)    │         │  pgvector/pgvector:0.8.2-pg18│      │
│  │                         │         │                              │      │
│  │  ┌───────────────────┐  │         │  - genereview_chapters       │      │
│  │  │ uvicorn worker(s) │──┼─asyncpg─┼─→ genereview_passages         │      │
│  │  │  FastAPI + MCP    │  │         │  - genereview_embeddings_*   │      │
│  │  └───────────────────┘  │         │  - genereview_active_embedding│      │
│  │                         │         │  - genereview_corpus_version │      │
│  │  ┌───────────────────┐  │         │  - genereview_refresh_log    │      │
│  │  │ APScheduler hourly│──┼─────────┘  - schema_migrations         │      │
│  │  │  release-watcher  │  │                                        │      │
│  │  │  (opt-in pull)    │  │   Volume: pg_data                      │      │
│  │  └───────────────────┘  │                                        │      │
│  │                         │                                        │      │
│  │  ┌───────────────────┐  │                                        │      │
│  │  │ Entrypoint:       │  │                                        │      │
│  │  │  download bundle  │──┼─httpx→ github.com/.../releases/latest  │      │
│  │  │  pg_restore -j    │  │                                        │      │
│  │  └───────────────────┘  │                                        │      │
│  │                         │                                        │      │
│  │  ┌───────────────────┐  │                                        │      │
│  │  │ EutilsClient      │──┼─httpx→ eutils.ncbi.nlm.nih.gov         │      │
│  │  │  (fallback only)  │  │  (only on ?fresh=true)                 │      │
│  │  └───────────────────┘  │                                        │      │
│  └─────────────────────────┘                                        │      │
└────────────────────────────────────────────────────────────────────────────┘
```

### Three runtime modes (mirroring phentrieve)

1. **`BUNDLE_URL` set (default)** — first boot downloads the release bundle,
   verifies SHA-256 against the manifest, `pg_restore -j $(nproc)` into
   Postgres, then starts serving.
2. **`BUILD_LOCAL=true`** — dev / offline. Runs the full ingest pipeline
   in-container against the live FTP archive.
3. **External Postgres** — `BUNDLE_URL=""`, `BUILD_LOCAL=false`, operator
   points `DATABASE_URL` at an externally-managed instance.

### Package layout

```
genereview_link/
├── corpus/            FTP fetch, NXML parse, chunk, side-data join, bundle pack
│   ├── archive.py     file_list.csv polling, tarball download/verify
│   ├── nxml.py        BITS parser → ChapterRecord, PassageRecord
│   ├── chunking.py    512-token windows within <sec>
│   ├── sidedata.py    /pub/GeneReviews/ side-files → NBK↔gene↔OMIM
│   └── bundle.py      pg_dump, manifest, tar.gz packaging
├── retrieval/         lexical SQL, dense rerank, RRF fusion
│   ├── lexical.py     three-tsquery search SQL (lifted from pubtator-link)
│   ├── embeddings.py  BGE-small provider, lazy load (lifted)
│   ├── rerank.py      RRF + section_priority (lifted, simplified)
│   └── repository.py  asyncpg pool wrapper, GeneReviewRepository
├── db/                migrations, migration runner
│   ├── migrate.py     ported from pubtator-link
│   └── migrations/    0001_base.sql, 0002_chapters.sql, ...
├── ingest/            orchestrator for CLI-driven local ingest
│   ├── pipeline.py    9-stage pipeline (used by CI builder and MODE 2)
│   └── parallel.py    ProcessPool + asyncpg COPY writers + pipelined encoder
├── api/               existing FastAPI routes + new ones
│   ├── eutils_client.py  unchanged (only invoked on ?fresh=true)
│   └── routes/        existing 5 + 3 new (passages, sections, debug)
├── services/          thin GeneReviewService orchestrating retrieval
├── models/            Pydantic (existing + new RankedPassage, CorpusVersion, LicenseNotice)
├── config.py          new env vars: DATABASE_URL, BUNDLE_URL, BUILD_LOCAL, ...
├── cli.py             new subcommands: ingest, embed, bundle, db migrate, ...
└── server_manager.py  FastMCP.from_fastapi() picks up new routes; explicit name map updated
```

## Database schema

Six tables plus `schema_migrations`. All migrations live under
`genereview_link/db/migrations/` named `NNNN_<topic>.sql` and applied in
lexical order by `db/migrate.py`.

### `genereview_chapters`

```sql
create table genereview_chapters (
    nbk_id              text primary key,
    short_name          text not null,
    title               text not null,
    pubmed_id           text,                              -- single PMID per chapter, from book-part-meta
    gene_symbols        text[] not null default '{}',
    omim_ids            text[] not null default '{}',
    authors             text,
    initial_pub_date    date,
    last_updated_date   date,
    corpus_version      text not null,
    nxml_relpath        text not null,
    raw_metadata        jsonb not null default '{}'::jsonb,
    ingested_at         timestamptz not null default now()
);

create index genereview_chapters_gene_symbols_gin
    on genereview_chapters using gin (gene_symbols);
create index genereview_chapters_omim_gin
    on genereview_chapters using gin (omim_ids);
create index genereview_chapters_pubmed_id_idx
    on genereview_chapters (pubmed_id) where pubmed_id is not null;
create index genereview_chapters_last_updated_idx
    on genereview_chapters (last_updated_date desc);
create index genereview_chapters_corpus_version_idx
    on genereview_chapters (corpus_version);
```

`gene_symbols` is an array because GeneReviews chapters routinely cover
multi-gene disorders. The canonical mapping comes from
`NBKid_shortname_genesymbol.txt`, not the NXML (NXML `<kwd-group>` is a
fallback cross-check).

### `genereview_passages`

```sql
create table genereview_passages (
    nbk_id              text not null references genereview_chapters(nbk_id) on delete cascade,
    passage_id          text not null,
    chapter_section     text not null,
    heading_path        text,
    section_level       int not null default 1,
    chunk_index         int not null,
    text                text not null,
    text_hash           text not null,
    char_count          int not null,
    token_estimate      int not null,
    corpus_version      text not null,
    search_vector       tsvector generated always as (
        to_tsvector('english',
            coalesce(heading_path, '') || ' ' ||
            chapter_section || ' ' ||
            text
        )
    ) stored,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id)
);

create index genereview_passages_search_vector_gin
    on genereview_passages using gin (search_vector);
create index genereview_passages_nbk_section_idx
    on genereview_passages (nbk_id, chapter_section);
create index genereview_passages_section_idx
    on genereview_passages (chapter_section);
create index genereview_passages_corpus_version_idx
    on genereview_passages (corpus_version);
```

`chapter_section` is normalized to a closed vocabulary: `summary`,
`diagnosis`, `clinical_features`, `management`, `genetic_counseling`,
`molecular_genetics`, `resources`, `references`, `other`. Unmatched titles
fall to `other`. Operator gets a "how many fell to `other`" metric in
`refresh_log.detail`.

### `genereview_embeddings_bge384`

```sql
create table genereview_embeddings_bge384 (
    nbk_id              text not null,
    passage_id          text not null,
    model_name          text not null default 'BAAI/bge-small-en-v1.5',
    model_revision      text,
    text_hash           text not null,
    embedding           vector(384) not null,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id),
    foreign key (nbk_id, passage_id)
        references genereview_passages(nbk_id, passage_id)
        on delete cascade
);

create index genereview_embeddings_bge384_hnsw_cosine
    on genereview_embeddings_bge384
    using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 200);
```

**Model-flexibility design** (fixes pubtator-link's hardcoded
`check (embedding_dim = 384)` pitfall): one table per embedding model. A
new model = a new migration creating a new table. No `CHECK` constraint on
dim; pgvector enforces dim at the column type already.

### `genereview_active_embedding`

```sql
create table genereview_active_embedding (
    id              int primary key default 1 check (id = 1),
    table_name      text not null default 'genereview_embeddings_bge384',
    model_name      text not null default 'BAAI/bge-small-en-v1.5',
    updated_at      timestamptz not null default now()
);
insert into genereview_active_embedding default values on conflict do nothing;
```

Singleton pointer. A/B comparison = populate a second table and flip the
pointer in one statement.

### `genereview_corpus_version`

```sql
create table genereview_corpus_version (
    version                 text primary key,
    file_list_etag          text,
    tarball_sha256          text,
    tarball_size_bytes      bigint,
    chapter_count           int,
    ingest_started_at       timestamptz not null,
    ingest_finished_at      timestamptz,
    ingest_status           text not null,
    is_active               boolean not null default false,
    notes                   text
);
create unique index genereview_corpus_version_active_unique
    on genereview_corpus_version (is_active) where is_active;
```

The partial unique index enforces "at most one active version." Atomic-swap
pattern: new rows ingested with `is_active=false`; on success, single
transaction flips active true on new + false on old.

### `genereview_refresh_log`

```sql
create table genereview_refresh_log (
    refresh_id              uuid primary key default gen_random_uuid(),
    check_time              timestamptz not null default now(),
    file_list_last_updated  text,
    decision                text not null,
    duration_ms             bigint,
    detail                  jsonb not null default '{}'::jsonb
);
create index genereview_refresh_log_time_idx
    on genereview_refresh_log (check_time desc);
```

### Disk estimate

| Component | Estimated size |
|---|---|
| Tables (chapters + passages + sidedata-derived data) | 400 MB – 1 GB |
| `search_vector` GIN index | 200–400 MB |
| `embeddings_bge384` rows (150K × 384 × 4B) | ~250 MB |
| HNSW index (m=16) | 600 MB – 1.5 GB |
| Other indexes | < 50 MB |
| **Total DB footprint** | **~3–5 GB** |

Postgres volume sized 10 GB gives comfortable headroom.

## Ingest pipeline (runs in CI builder, not on the VPS)

Nine stages, fully idempotent, resumable on crash. State machine tracked in
`genereview_corpus_version.ingest_status`.

```
1. check_remote_version    HEAD/GET file_list.csv → extract NBK1116 row
2. download_tarball        range-resumable GET of gene_NBK1116.tar.gz; sha256 verify
3. download_sidedata       asyncio.gather over 6 side-data files
4. parse_nxml              ProcessPoolExecutor (N=min(cpu_count, 8)) — BITS parser
5. chunk                   folded into worker fn — 512-token windows within <sec>
6. write_passages          4 concurrent asyncpg copy_records_to_table writers
7. backfill_embeddings     pipelined: 1 encoder + 2 writers; batch 256
8. atomic_swap             single tx: flip is_active true on new, false on old
9. cleanup                 retain last 2 versions; cascade-delete older
```

Detailed flow in section "Parallelization model" below.

### NXML parsing notes

- Use `defusedxml.lxml` per AGENTS.md
- BITS root: `<book-part book-part-type="chapter">`
- Title at `book-part-meta/title-group/title`
- PMID at `book-part-meta/book-part-id[@pub-id-type='pmid']` (populates `genereview_chapters.pubmed_id`)
- Authors at `book-part-meta/contrib-group/contrib/name`
- Body at `book/book-part/body`
- Recursive `<sec>` walk; capture `<title>` and `<p>` text
- Skip `<fig>`, `<table-wrap>`, `<ref-list>` in v1 (Phase 7 enhancement)
- Section canonicalization via regex table; unmatched → `other`
- Skip-and-log policy on parse failure per chapter

### Side-data files

Six files fetched from `https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/`:

- `GRtitle_shortname_NBKid.txt` — title↔short-name↔NBK id
- `NBKid_shortname_genesymbol.txt` — NBK↔gene-symbol (canonical for `gene_symbols[]`)
- `NBKid_shortname_OMIM.txt` — NBK↔OMIM id (canonical for `omim_ids[]`)
- Plus three additional cross-reference files (UniProt, etc.)

Parsed into in-memory dicts keyed by NBK id; joined during stage 4.

### Failure modes

| Failure | Recovery |
|---|---|
| Crash between stages 2–6 | Resume from stage indicated by populated rows for the in-progress version; stages 4–6 are idempotent (`on conflict do nothing` on chapters; delete-then-insert per chapter on passages) |
| NCBI 503 / network blip | httpx retries with exponential backoff (max 5, cap 60s); on exhaustion mark `status=failed`, next run retries |
| Single NXML malformed | Skip chapter, log to `refresh_log.detail.skipped_chapters[]`; ingest continues |
| Embedding model download fails | Stage 7 fails; status `completed_without_embeddings`; lexical-only retrieval still works; next run retries embedding backfill independently |
| Disk fills during extract | tarfile is streamed; passages flushed per chapter; partial passages for the last chapter; corpus_version stays inactive; next run re-downloads cleanly |

## Parallelization model (CI builder)

Per-chapter work is fully independent. Pipeline rebuilds as a
producer-consumer fan-out with bounded backpressure queues.

| Stage | Parallelism | Tool |
|---|---|---|
| 1. check_remote_version | none | single async call |
| 2. download_tarball | none | single async stream |
| 3. download_sidedata | 6-way fan-out | `asyncio.gather` |
| 4. parse_nxml | N=min(cpu_count, 8) | `ProcessPoolExecutor` |
| 5. chunk | same workers as 4 | folded into worker fn |
| 6. write_passages | 4 concurrent writers | asyncpg `copy_records_to_table` |
| 7. backfill_embeddings | 1 encoder + 2 writers (pipelined) | shared BGE model + `asyncio.to_thread` |
| 8. atomic_swap, 9. cleanup | none | single tx |

### Stages 4–6 (parse → chunk → write)

```
tarfile stream ──► raw_nxml_queue ─► ProcessPool (N parse_chunk_fn) ─► record_queue ─► 4 writer coros (COPY)
                   (bytes/chap)                                                            │
                                                                                           ▼
                                                                                       Postgres
```

- Reader streams `tarfile` members into `raw_nxml_queue` (maxsize=2N) —
  blocks on full → natural backpressure
- ProcessPool worker (`corpus.nxml.parse_and_chunk_one`) — pure function,
  returns `(ChapterRecord, list[PassageRecord])`
- Writer coroutines drain `record_queue` in batches of 50 chapters,
  one COPY per batch
- Expected: stage 4+5+6 ~30–60s on 8-core CPU

### Stage 7 (embeddings — pipelined, not replicated)

```
fetcher → fetch_q (maxsize=2) → encoder → encoded_q (maxsize=2) → 2 writers → Postgres
```

- One BGE model instance — torch's intra-op parallelism saturates CPU on
  a single batch; replicating would waste 120 MB per worker
- `model.encode(batch_size=256, normalize_embeddings=True)` in
  `asyncio.to_thread` — yields the event loop during heavy compute
- Two writers overlap with the encoder: while writer N COPYs into Postgres,
  the encoder is on batch N+1
- Expected: ~5 min CPU / ~1–2 min GPU for 150K passages

### Configurable knobs

```python
INGEST_PARSE_WORKERS: int = min(os.cpu_count() or 4, 8)
INGEST_DB_WRITERS: int = 4
INGEST_EMBED_BATCH_SIZE: int = 256
INGEST_EMBED_WRITERS: int = 2
INGEST_EMBED_DEVICE: str = "auto"
```

All env-overridable: `GENEREVIEW_INGEST_PARSE_WORKERS`, etc.

### Total runtime (parallelized)

| Stage | Single-threaded | Parallelized |
|---|---|---|
| 1–3 (network) | 3–5 min | 3–5 min |
| 4–5 (parse + chunk) | 40 s – 1 min | 8–15 s (8 workers) |
| 6 (write) | 30–60 s | 8–15 s (4 writers + COPY) |
| 7 (embed) | 8–12 min | 4–6 min CPU / 1–2 min GPU |
| 8–9 | < 1 s | < 1 s |
| **Total** | **~15 min** | **~9–11 min CPU / ~5–7 min GPU** |

## Retrieval stack

### Repository (`retrieval/repository.py`)

```python
class GeneReviewRepository:
    def __init__(self, pool: asyncpg.Pool, *, acquire_timeout_s: float = 5.0): ...

    # ---- Chapter-level (replace EutilsClient.scrape_* on hot path) ----
    async def get_chapter_by_gene(self, gene_symbol: str) -> ChapterRow | None: ...
    async def get_chapter_by_nbk(self, nbk_id: str) -> ChapterRow | None: ...
    async def list_chapters(self, *, limit: int, offset: int) -> list[ChapterRow]: ...

    # ---- Passage retrieval ----
    async def search_passages(
        self, query: str, *,
        gene_symbol: str | None = None,
        nbk_id: str | None = None,
        sections: list[str] | None = None,
        limit: int = 20,
    ) -> list[LexicalPassageRow]: ...
    async def get_section(self, nbk_id: str, chapter_section: str) -> list[PassageRow]: ...

    # ---- Embeddings ----
    async def dense_scores_for_passages(
        self, query_vector: list[float], passage_ids: list[str], *, model_table: str
    ) -> dict[str, float]: ...

    # ---- Operational ----
    async def active_corpus_version(self) -> CorpusVersion: ...
    async def active_embedding_table(self) -> str: ...
```

### Lexical SQL — three-tsquery pattern, lifted

`retrieval/lexical.py` ports pubtator-link's `search_passages` query
verbatim. Rename table, drop `review_id` scoping. Same weighting
(`phrase × 3.0 + strict × 2.0 + recall × 1.0`), same weak-recall penalty
for long queries with low overlap. The recall_terms array is built in
Python (3+ char tokens, deduped, lowercased). Filters: `gene_symbol` via
array overlap on chapter join, `nbk_id` via equality, `sections` via
`chapter_section = any($)`.

### RRF + section-priority — lifted and simplified

`retrieval/rerank.py` ports pubtator-link's `embedding_rerank.py`:

- `rerank_with_embeddings(lexical_rows, dense_scores, rrf_k=60)`
- Guarded sections: `{"references"}` only
- Strategy name: `"lexical_top_k_dense_rrf"`
- `rerank_key` simpler than pubtator's (one source — the FTP archive — so
  no `source_priority` table needed)

```python
SECTION_PRIORITY = {
    "summary":            0,
    "diagnosis":          0,
    "clinical_features":  1,
    "management":         1,
    "genetic_counseling": 2,
    "molecular_genetics": 2,
    "resources":          5,
    "other":              7,
    "references":        50,
}
```

### Query-time flow

1. Lexical query returns top-50 candidates
2. Embed query once with `bge_query_text("Represent this sentence for searching relevant passages: " + query)`
3. Bulk-fetch dense scores via indexed lookup on the candidate set
4. Hand both lists to `rerank_with_embeddings` → final ranked passages

HNSW index stays off the hot path for filtered queries; deterministic
latency.

## API surface

### Existing routes (5) — preserved

| Path | Internal change |
|---|---|
| `GET /search/{gene_symbol}?retmax=N` | Serves from `genereview_chapters.gene_symbols` GIN scan; falls back to `EutilsClient.search_genereviews` only on `?fresh=true` |
| `GET /abstract/{pubmed_id}` | Backed by chapter metadata when PMID maps to an indexed chapter (`genereview_chapters.pubmed_id` lookup); falls through to `EutilsClient.fetch_abstract` otherwise |
| `GET /links/{pubmed_id}` | Bookshelf URLs reconstructed from `nbk_id`; same fallback policy |
| `GET /fulltext/{nbk_id}?sections=X,Y` | Serves indexed passages reassembled per section; `sections=` narrows to `chapter_section in (...)` |
| `GET /genereview/{gene_symbol}` | Routes through repository; same composite response shape |

**Additive response fields** (all existing models gain optional):
- `corpus_version: str | None` — the active corpus version that served this response
- `license: LicenseNotice | None` — copyright notice + terms URL

Old clients that don't read these keep working.

**`?fresh=true`** on any route: hit `EutilsClient` live, return data with
`corpus_version = "live:<iso8601>"`. Logged separately for observability.

**Structured 404** when index lookup misses and `fresh=true` not set:

```json
{
  "error": "not_yet_indexed",
  "gene_symbol": "FOO",
  "corpus_version": "2026-05-10",
  "hint": "Pass ?fresh=true to fetch from NCBI live"
}
```

### New routes (v1) — 3

| Path | Purpose |
|---|---|
| `GET /passages/search?q=...&gene=...&nbk=...&sections=...&limit=N&rerank=rrf\|lexical\|off` | Cross-corpus passage retrieval. Returns `RankedPassage[]` with score breakdown. Default `rerank=rrf` |
| `GET /chapters/{nbk_id}/sections/{section}` | Reassembled section content with `heading_path` preserved. Single-section LLM-prompt-shaped response |
| `GET /debug/ranking?q=...` (gated behind `DEBUG_RANKING_ENABLED`) | Reranker introspection: full score breakdown per candidate (lexical components, dense_score, dense_rank, rrf_score, section_priority, final_position) |

### New routes (v1.1) — 1

| Path | Purpose |
|---|---|
| `GET /references/by-pmid/{pmid}` | Reverse lookup: which chapters cite this PMID? Requires NXML `<ref-list>` ingestion (Phase 7) |

### MCP tools

Registration unchanged in shape — `FastMCP.from_fastapi()` with explicit
name mapping in `server_manager.py`. The route map already filters out
`/debug/`-prefixed routes.

| FastAPI route | MCP tool name | Stability |
|---|---|---|
| `GET /search/{gene}` | `search_genereviews` | unchanged |
| `GET /abstract/{pmid}` | `get_abstract` | unchanged |
| `GET /links/{pmid}` | `get_links` | unchanged |
| `GET /fulltext/{nbk_id}` | `get_fulltext` | unchanged |
| `GET /genereview/{gene}` | `get_genereview_summary` | unchanged |
| `GET /passages/search` | `search_passages` | NEW v1 |
| `GET /chapters/{nbk_id}/sections/{section}` | `get_chapter_section` | NEW v1 |
| `GET /references/by-pmid/{pmid}` | `find_chapters_citing_pmid` | NEW v1.1 |
| `GET /debug/ranking` | excluded from MCP | dev-only |

### Service layer

`GeneReviewService` shrinks. Methods become thin orchestrators that
dispatch on `fresh=False|True`. `alru_cache` stays on public methods — a
short-lived in-process cache on top of the DB-backed corpus still helps
hot-gene traffic spikes.

### New Pydantic models

- `CorpusVersion(version: str, last_updated: datetime, is_active: bool)`
- `LicenseNotice(copyright: str, terms_url: str)`
- `RankedPassage(passage_id, nbk_id, gene_symbols, chapter_section, heading_path, text, char_count, score_breakdown: ScoreBreakdown)`
- `ScoreBreakdown(lexical_rank, lexical_rank_components: {phrase, strict, recall}, dense_score?, dense_rank?, rrf_score?, section_priority, final_position)`
- `NotYetIndexedError` — raised as `HTTPException(404, detail=...)` by a single FastAPI exception handler

Existing models (`GeneReview`, `GeneReviewSection`, `FullTextData`,
`FullTextMetadata`, `AbstractData`, `LinkData`, `SearchResult`) gain
optional `corpus_version` and `license` fields. Field-additive only.

## CI bundle distribution

### Workflow shape (`.github/workflows/build-corpus.yml`)

```
schedule: cron 0 6 * * MON       # weekly Monday 06:00 UTC
on: workflow_dispatch
```

```
check-job
  HEAD file_list.csv → extract NBK1116 "Last Updated"
  compare to latest release tag (corpus-YYYY-MM-DD)
  → no change? exit 0 (skipped)
  → changed? emit corpus_version output
                                  │
                                  ▼
build-corpus-job (matrix per embedding flavor)
  services: postgres: pgvector/pgvector:0.8.2-pg18
  - checkout, uv sync
  - genereview-link db migrate
  - genereview-link ingest      (parallelized pipeline)
  - pg_dump -Fc -f corpus.dump genereview
  - generate manifest.json (checksums, counts, schema versions)
  - tar czf bundle.tar.gz manifest.json corpus.dump sidedata/
  - upload-artifact
                                  │
                                  ▼
release-job
  softprops/action-gh-release@v3
  tag: corpus-2026-05-10
  attach all bundles + each manifest.json
  update `latest` pointer if all matrix jobs succeeded
```

### Bundle format

```
genereview-corpus-2026-05-10-bge384.tar.gz
├── manifest.json
├── corpus.dump          # pg_dump -Fc (custom format) — parallel-restorable
└── sidedata/
    ├── GRtitle_shortname_NBKid.txt
    ├── NBKid_shortname_genesymbol.txt
    └── NBKid_shortname_OMIM.txt
```

### `manifest.json` schema

```json
{
  "manifest_version": "1",
  "bundle_format": "tar.gz",
  "corpus_version": "2026-05-10",
  "tarball_source_sha256": "<sha256 of gene_NBK1116.tar.gz>",
  "tarball_last_updated": "2026-05-10T03:32:37Z",
  "chapter_count": 912,
  "passage_count": 153420,
  "embedding": {
    "model_name": "BAAI/bge-small-en-v1.5",
    "dimension": 384,
    "distance_metric": "cosine",
    "active_table": "genereview_embeddings_bge384"
  },
  "postgres": {
    "major_version": "18",
    "pgvector_version": "0.8.2"
  },
  "schema_migrations": ["..."],
  "genereview_link_version": "0.4.0",
  "created_at": "2026-05-11T06:14:00Z",
  "created_by": "github-actions:build-corpus.yml#42",
  "license": {
    "copyright": "© 1993-2026 University of Washington",
    "terms_url": "https://www.ncbi.nlm.nih.gov/books/NBK138602/"
  },
  "checksums": {
    "corpus.dump": "sha256:<...>",
    "sidedata/GRtitle_shortname_NBKid.txt": "sha256:<...>",
    "sidedata/NBKid_shortname_genesymbol.txt": "sha256:<...>",
    "sidedata/NBKid_shortname_OMIM.txt": "sha256:<...>"
  }
}
```

### Container entrypoint

```python
async def entrypoint():
    if await db.has_active_corpus():
        return await serve()  # hot path

    if settings.BUNDLE_URL:
        # MODE 1
        bundle = await download_bundle(settings.BUNDLE_URL)
        verify_manifest_checksums(bundle)
        verify_compatibility(bundle.manifest)  # pg version, pgvector version
        await pg_restore(bundle.corpus_dump, jobs=os.cpu_count())
        await db.mark_active(bundle.manifest.corpus_version)
        return await serve()

    if settings.BUILD_LOCAL:
        # MODE 2 (dev/offline)
        await run_full_ingest()
        return await serve()

    # MODE 3 (external Postgres)
    return await serve()
```

### Optional release watcher

```python
@scheduler.scheduled_job('cron', minute=17)  # hourly :17
async def check_for_new_release():
    latest = await github.get_latest_release_manifest()
    active = await db.active_corpus_version()
    if latest.corpus_version > active.version:
        log.info("newer corpus available", latest=latest.corpus_version)
        if settings.AUTO_PULL_RELEASES:
            await pull_and_swap_via_schema(latest)
        # else: surface in /health/corpus, operator-driven
```

`AUTO_PULL_RELEASES` defaults to `false` — production swaps are
operator-driven. Hot-swap via schema rename: `pg_restore --schema=staging`
into a new schema, flip `search_path` for the app role atomically, drop
old schema after a retention window.

## Testing strategy

### Test pyramid

**Unit (no I/O, no DB):**

| Module | Coverage |
|---|---|
| `corpus/nxml.py` | parse_and_chunk_one against 6 fixture NXML files: typical chapter, multi-gene, missing pub-date, malformed XML, deep nesting, unicode |
| `corpus/chunking.py` | 512-token windowing, overlap, never-cross-`<sec>`, heading_path assembly, canonical section normalization |
| `corpus/sidedata.py` | 3 side-data files; ASCII vs UTF-8; missing rows; multi-gene NBK ids |
| `retrieval/rerank.py` | RRF math, guarded-section handling, section_priority tiebreaks (ported from pubtator-link's tests) |
| `retrieval/lexical.py` | `_recall_terms` tokenization, `_recall_tsquery` OR-joining |
| `corpus/bundle.py` | manifest schema, checksum verification, naming convention |

**Integration (real Postgres via testcontainers):**

| Test | What it covers |
|---|---|
| `test_repository_lexical.py` | three-tsquery SQL against seeded mini-corpus; phrase > strict > recall ordering; weak-recall penalty |
| `test_repository_dense.py` | dense_scores_for_passages returns cosine in [-1, 1]; handles empty / missing |
| `test_ingest_end_to_end.py` | 3-chapter fixture tarball: download → parse → chunk → write → embed → activate; atomic swap; cleanup |
| `test_bundle_round_trip.py` | pg_dump → pg_restore into fresh DB; manifest checksums match; HNSW rebuilds; query parity |
| `test_migrations.py` | `apply_migrations` idempotent; partial-applied state recovers |

**Route tests:** all 5 existing routes get index-backed + `?fresh=true`
variants. Plus tests for the 2 new v1 routes. Additive fields validated.

**Smoke / acceptance (CI nightly):** pulls latest published bundle into
throwaway Postgres, runs ~10 canonical queries against known chapters.
Catches "bundle is valid but ranks badly" regressions.

### Fixtures

- Keep `tests/fixtures/NBK1247_BRCA1.html`, `NBK1311_Huntington.html`
  (still used by `?fresh=true` fallback tests)
- Add `tests/fixtures/nxml/` with 6 canonical NXML fixtures
- Add `tests/fixtures/sidedata/` with abbreviated side-data files (~20 rows each)
- Add `tests/fixtures/bundles/mini.tar.gz` — 3-chapter bundle for round-trip tests

### Eval set

`tests/eval/genereviews_queries.jsonl` — ~30 hand-curated
`(query, expected_chapter, expected_section)` triples. Run nightly against
latest bundle; compute MRR@10 + section-precision@5. Regression threshold:
drop > 5% on either metric blocks the release.

## Rollout phases

Phased to keep `main` shippable at every step. Each phase = one PR with
passing CI.

### Phase 1 — schema & migrations (no behavior change)

- Add `genereview_link/db/` with `migrate.py` and migration files
- Add `docker-compose.yml` Postgres service (pgvector image)
- Wire `DATABASE_URL` env var; entrypoint runs migrations on boot
- No route changes; existing routes still use `EutilsClient`
- **Done when:** fresh `docker compose up` provisions empty schema; `make test` green.

### Phase 2 — corpus ingest pipeline (CLI only)

- Add `genereview_link/corpus/` (archive, nxml, chunking, sidedata)
- Add `genereview-link ingest` CLI subcommand
- Parallelized as designed
- No embeddings yet; lexical-only schema active
- **Done when:** running CLI locally populates Postgres with 900+ chapters; psql queries return sensible results.

### Phase 3 — embedding backfill

- Add `retrieval/embeddings.py` (BGE-small provider, lazy load)
- Add `genereview-link embed` CLI subcommand
- Add migration with embeddings table + HNSW index
- **Done when:** post-ingest, `embed` populates ~150K vectors.

### Phase 4 — retrieval layer

- Add `retrieval/` (lexical SQL, RRF rerank, repository)
- Unit + integration tests for ranking
- Still no route changes — repository exists but unused by routes
- **Done when:** integration tests pass; eval set runs against local DB.

### Phase 5 — route migration (user-visible change)

- `GeneReviewService` flipped to query repository by default; `?fresh=true` reaches `EutilsClient`
- New routes: `/passages/search`, `/chapters/{nbk_id}/sections/{section}`, `/debug/ranking`
- Existing 5 routes preserved with additive `corpus_version`/`license`
- Structured 404 for not-yet-indexed
- **Done when:** all existing route tests pass; new route tests pass; curl checks against populated local DB return expected shapes.

### Phase 6 — CI bundle workflow

- Add `.github/workflows/build-corpus.yml`
- Add `corpus/bundle.py` for packaging + manifest
- Add `corpus/bundle_download.py` for runtime retrieval
- Three-mode entrypoint logic in `cli.py` startup hook
- First successful release: `corpus-YYYY-MM-DD` tag with bundle attached
- **Done when:** workflow_dispatch run produces downloadable bundle; fresh `docker compose up` with `BUNDLE_URL` set restores it under 5 min.

### Phase 7 — references + provenance (v1.1)

- Extend NXML parser to capture `<ref-list>`
- Add `genereview_chapter_references` table
- Add `/references/by-pmid/{pmid}` route + MCP tool
- Cross-references between chapters
- **Done when:** can answer "which GeneReviews cite PMID X".

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| BITS NXML parser edge cases | Medium | Medium | Skip-and-log policy; refresh_log surfaces failures; CI eval set flags ranking regressions |
| HNSW build time grows as corpus expands | Low | Low | ~2 min for 150K vectors today; pg_restore parallel jobs absorbs it |
| pgvector version mismatch between CI builder and VPS Postgres | Medium | High | Manifest enforces `postgres.major_version` + `pgvector.version`; entrypoint refuses mismatched bundles |
| Bundle size grows past GitHub Releases 2 GB asset limit | Low | Medium | Currently estimated 300–600 MB; 3–5× headroom |
| GH Actions runner OOMs on full ingest + pg_dump | Medium | Medium | Fallback to larger runner or split parse/embed/dump into separate jobs sharing artifact |
| BGE-small ranking poor on biomedical terms | Medium | Medium | Eval set catches this; model-table pattern lets v1.1 add `_specter2` without schema mutation |
| `?fresh=true` floods NCBI E-utilities | Low | High | Existing rate limiter preserved (0.11/0.34s); add per-IP request limit on fresh path; metrics alarm at sustained > 1 req/s |
| Live-scraper drift silent because nobody hits fresh path | High | Low | Nightly CI run hits fallback against 3 canonical chapters |
| NCBI changes file_list.csv format or tarball path | Low | High | check-job fails early; existing release stays live; GitHub Actions failure email |
| Stale corpus served while chapter retracted upstream | Low | Medium | `/health/corpus` returns `{version, age_days}`; documented monitoring guidance |
| App role accidentally has write to passages | Low | Medium | Migration grants ingest-role write, app-role read-only; integration test verifies |

## Observability

- `/health` → `{status, corpus_version, corpus_age_days, embeddings_active_model}`
- `/health/corpus` → full corpus_version row
- Structured logs per search: `{query, lexical_top_k_size, dense_active, rrf_k, final_size, latency_ms, corpus_version}`
- Prometheus metrics (existing `ENABLE_METRICS`):
  `genereview_search_duration_seconds`,
  `genereview_fresh_fallback_total`,
  `genereview_corpus_version_info` (gauge labeled by version)

## Deferred to later

- Cross-encoder reranker (`bge-reranker-v2-m3`) — pattern decided, not shipping v1
- Multi-model A/B via `genereview_active_embedding` pointer — infrastructure exists, only one model populated at v1
- `<ref-list>` + reference graph — Phase 7
- Table / figure extraction from NXML
- Annotations / saved searches / multi-user state — not precluded by schema
- Cross-encoder + `nomic-embed-text-v1.5` truncated to 384d as drop-in upgrade if BGE-small ranking gap emerges

## Decisions explicitly locked

- pgvector + asyncpg + tsvector + RRF k=60 + BGE-small-en-v1.5(384d)
- `pg_dump -Fc` bundle format
- CI-baked + GitHub Releases distribution
- Three runtime modes (download / build / external)
- Five existing routes preserved with additive fields; four new routes (3 in v1, 1 in v1.1)
- `?fresh=true` opt-in fallback; structured 404 for not-yet-indexed
- Decomposed package layout (corpus / retrieval / db / ingest / api / services / models)
- Per-model embeddings tables (no `embedding_dim` CHECK constraint)
- License notice attached to every response
