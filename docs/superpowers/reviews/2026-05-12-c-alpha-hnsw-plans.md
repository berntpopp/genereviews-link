# C-alpha HNSW + Filter EXPLAIN ANALYZE Review

**Date:** 2026-05-12
**Branch:** feat/ranking-c-alpha
**Script:** `scripts/measure_dense_filter_plans.py`
**Artifact:** `c_alpha_hnsw_plans.json`

Database: `postgresql://genereview:genereview@127.0.0.1:5436/genereview`
Embedding table: `genereview_embeddings_bge384` (40 853 passages, 384-dim BGE)
Dummy query vector: `[0.0] * 384` (zero vector; planner accepted it)
top_k: 200

---

## Results Table

| case | top node_type | inner index | time_ms | rows |
|---|---|---|---|---|
| no_filter | Limit | HNSW cosine scan | 36.5 | 200 |
| gene_only | Limit | Bitmap (GIN) + pkey lookup | 0.9 | 58 |
| nbk_id_only | Limit | Merge Join pkey | 0.4 | 47 |
| section_only | Limit | HNSW cosine scan | 331.6 | 200 |
| gene_plus_section | Limit | Bitmap (GIN + nbk_section) | 1.0 | 32 |
| heading_path_only | Limit | Gather Merge / Seq Scan | 28.0 | 200 |

Full planner JSON (with BUFFERS) is in `c_alpha_hnsw_plans.json`.

---

## Plan Trees

### no_filter
```
Limit
  Nested Loop
    Nested Loop
      Index Scan (genereview_embeddings_bge384_hnsw_cosine)   <-- HNSW
      Index Only Scan (genereview_passages_pkey)
    Memoize
      Index Only Scan (genereview_chapters_pkey)
```

### gene_only
```
Limit
  Sort
    Nested Loop
      Nested Loop
        Bitmap Heap Scan
          Bitmap Index Scan (genereview_chapters_gene_symbols_gin)
        Index Only Scan (genereview_passages_pkey)
      Index Scan (genereview_embeddings_bge384_pkey)         <-- exact pkey, no HNSW
```

### nbk_id_only  (HNSW bypass branch)
```
Limit
  Sort
    Merge Join
      Index Only Scan (genereview_passages_pkey)
      Index Scan (genereview_embeddings_bge384_pkey)         <-- exact pkey, no HNSW
```

### section_only
```
Limit
  Nested Loop
    Nested Loop
      Index Scan (genereview_embeddings_bge384_hnsw_cosine)  <-- HNSW
      Index Scan (genereview_passages_pkey)
    Memoize
      Index Only Scan (genereview_chapters_pkey)
```

### gene_plus_section
```
Limit
  Sort
    Nested Loop
      Nested Loop
        Bitmap Heap Scan
          Bitmap Index Scan (genereview_chapters_gene_symbols_gin)
        Bitmap Heap Scan
          Bitmap Index Scan (genereview_passages_nbk_section_idx)
      Index Scan (genereview_embeddings_bge384_pkey)         <-- exact pkey, no HNSW
```

### heading_path_only
```
Limit
  Gather Merge
    Sort
      Nested Loop
        Nested Loop
          Seq Scan (genereview_passages)                     <-- full seq scan!
          Index Scan (genereview_embeddings_bge384_pkey)
        Index Only Scan (genereview_chapters_pkey)
```

---

## Conclusions

### HNSW used exactly where expected

`no_filter` and `section_only` are the two cases that should exercise iterative
HNSW scan (setup statements `SET LOCAL hnsw.iterative_scan = 'relaxed_order'`
and `SET LOCAL hnsw.ef_search = 200` applied). Both plans show
`Index Scan (genereview_embeddings_bge384_hnsw_cosine)` at the inner driver,
confirming the iterative path is active.

### nbk_id_only correctly bypassed HNSW

As designed, the `nbk_id` sole-filter branch returns empty setup statements and
uses `ORDER BY embedding <=> $1` with the primary key. The planner chose a
Merge Join over both pkey index scans -- no HNSW index appears anywhere in the
plan. The bypass is functioning.

### Gene filter suppresses HNSW (expected, not a bug)

`gene_only` and `gene_plus_section` both fall into the iterative-HNSW code path
in the SQL builder (setup statements are returned), but the planner overrides
the index choice: it prefers GIN bitmap scan on `gene_symbols` + exact pkey
lookup on the embedding table. This is correct behaviour -- the gene filter is
highly selective (HFE: 1 chapter, BRCA1: 2 chapters) so exhaustive pkey
re-ranking is cheaper than iterative HNSW. Planning time is sub-1 ms and
execution is under 1 ms.

### heading_path_only triggers a full Seq Scan -- action required

`heading_path_contains` is an `ILIKE '%Prevention%'` predicate. There is no
GIN/trigram index on `heading_path`, so the planner resorts to a parallel
Seq Scan over the full passages table (40 853 rows) before joining embeddings.
At 28 ms this is acceptable for a development corpus, but will degrade linearly
with corpus size.

**Recommended fix:** add a `pg_trgm` GIN index on `genereview_passages.heading_path`:

```sql
CREATE INDEX CONCURRENTLY genereview_passages_heading_trgm
  ON genereview.genereview_passages
  USING GIN (heading_path gin_trgm_ops)
  WHERE heading_path IS NOT NULL;
```

This would allow `ILIKE '%...%'` to use an index scan and should push
`heading_path_only` from ~28 ms into the low-single-digit ms range.

### section_only is the slowest HNSW case (331 ms)

The `chapter_section` filter (`= any($N::text[])`) does not restrict to a
narrow subset -- "management" covers ~9 858 of 40 853 passages (24%). HNSW
iterative scan must expand `ef_search = 200` candidates, filter 24% acceptance,
and iterate until 200 qualifying rows are found. This is the expected cost of a
broad section filter. If `section_only` latency becomes a concern, options are:

- Tighten `ef_search` for broad-section queries.
- Add a partial HNSW index per section (large schema overhead, probably not
  worth it at this corpus size).
- Accept the latency: section_only is an unusual query shape in practice.

### Planner accepted the zero vector

`[0.0] * 384` was passed without error. No fallback to a random seed vector
was needed.

---

## Sign-off

Operator: _(fill in)_
Date reviewed: 2026-05-12
