# Changelog

All notable changes to GeneReview-Link are documented in this file.

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) conventions.
Versioned releases also tag the corresponding commit; deployment milestones are noted
as phase tags where a semver release has not yet been cut.

---

## Unreleased

### Fixed

- Applied `CACHE_TTL_HOURS` to all `GeneReviewService` `alru_cache` wrappers;
  cached live/scraped summaries now expire according to configuration.
- STDIO MCP startup now runs the same lifecycle initialization as HTTP/unified
  startup, so corpus-backed tools have repository, embedder, gene index, and
  corpus-version state.
- Hardened corpus bundle bootstrap: manifest checksums are verified against
  in-tarball bytes before extraction, unexpected/duplicate members are rejected,
  extraction uses `filter="data"`, and `pg_restore` falls back when
  `os.cpu_count()` is unavailable.
- `search_genereviews` empty successful results now include a recovery hint and
  executable `_meta.next_commands` pointing to `search_passages`.
- `get_abstract` now rejects non-numeric PubMed IDs with
  `422 invalid_pubmed_id` before calling live NCBI.
- `get_fulltext` and embedded `get_genereview_summary.full_text_data` now return
  canonical `NBK...` identifiers.
- `get_genereview_summary` can still resolve a Bookshelf chapter when
  `include_links=false`; include flags control response shape, not resolution.

### Changed

- asyncpg pool default max size is now 20.
- Postgres `search_path` is configured with asyncpg `server_settings`, avoiding
  per-query `set search_path` round trips and surviving pool release/reset.
- Added asyncpg tuning settings:
  `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S`,
  `DATABASE_COMMAND_TIMEOUT_S`, and `DATABASE_STATEMENT_CACHE_SIZE`.
- `get_table` exposes a schema-level `table_id` pattern for earlier malformed
  input rejection.
- Removed the dead `mcp_custom_names` identity map from MCP server setup.
- Split server lifecycle/bootstrap code into `genereview_link/server_lifecycle.py`
  to keep `server_manager.py` under the module size budget.

---

## MCP LLM-ergonomics pass 2

Four-phase work (Trust -> Discovery -> Content -> Polish) lifting the MCP server from
a consumer LLM rating of 8.2/10 toward ~9.2/10 by fixing broken metadata promises,
making tables retrievable, adding discovery affordances, and landing polish items.

### Breaking changes

1. **`score_breakdown` removed from `search_passages` response by default**

   - *What changed:* The `score_breakdown` object (per-passage lexical/dense component
     scores) is no longer included in `search_passages` results unless explicitly
     requested.
   - *Why:* Reduces response size by ~30% for the common case; score internals are
     rarely actionable for downstream consumers.
   - *Migration:* Pass `include=score_breakdown` as a query parameter to restore the
     field.
     ```
     GET /passages?q=...&include=score_breakdown
     ```

2. **`get_passage` response is always wrapped**

   - *What changed:* `get_passage` now always returns a wrapper object
     `{passage, neighbors_before, neighbors_after, has_more_before, has_more_after}`
     rather than the passage directly.
   - *Why:* Exposes the neighbor window that LLMs need for context without a separate
     round-trip.
   - *Migration:* Read the focal passage from `response.passage.*` instead of the
     response root. Neighbor passages are in `response.neighbors_before` and
     `response.neighbors_after`.

3. **`get_chapter_section` no longer returns `concatenated_text` by default**

   - *What changed:* The `concatenated_text` field in chapter-section responses is
     omitted unless explicitly requested.
   - *Why:* Full-section concatenation can exceed safe context windows; opt-in ensures
     consumers that cannot handle large payloads are not silently broken.
   - *Migration:* Pass `include=concatenated_text` to restore the field, or switch to
     `get_passage` / `search_passages` for targeted retrieval.
     ```
     GET /chapters/{nbk_id}/sections/{section_id}?include=concatenated_text
     ```

4. **Narrative `passage_id`s may have shifted `chunk_index`**

   - *What changed:* Table passages are now interleaved into the chunk sequence at their
     document position, which may shift the `chunk_index` component of `passage_id` for
     narrative (non-table) passages in chapters that contain tables.
   - *Why:* Interleaving preserves reading order and enables faithful reconstructions of
     chapter content.
   - *Migration:* Do not store `passage_id` values across corpus rebuilds. Re-resolve
     cached IDs via `search_passages` using the original query terms.

5. **Ordinal `table_id`s may shift across rebuilds**

   - *What changed:* `table_id` values are assigned ordinally per chapter based on
     document order. If NCBI inserts or removes tables between rebuilds, all subsequent
     `table_id` values in that chapter shift.
   - *Why:* Ordinal assignment was chosen over NCBI internal IDs because Bookshelf does
     not expose stable table identifiers in the NXML.
   - *Migration:* Do not cache `table_id` values long-term. Re-resolve via
     `get_chapter_metadata` to obtain the current table list and pick the correct
     ordinal.

6. **License content is available as both a tool and resource**

   - *What changed:* License and attribution text is exposed through
     `get_license`, `GET /license`, and the cacheable `genereview://license`
     MCP resource.
   - *Why:* Static reference material works well as a resource, while retaining
     the tool keeps older MCP clients and scripted smoke tests compatible.
   - *Migration:* No migration is required. Prefer `genereview://license` for
     cacheable clients; `call_tool("get_license")` remains supported.

7. **Invalid gene symbol now returns a structured 400 instead of empty results**

   - *What changed:* Querying with a gene symbol that is not in the indexed corpus now
     returns HTTP 400 with a structured error body rather than an empty results list.
   - *Why:* Silent empty results were indistinguishable from a gene with no matching
     passages, causing LLMs to hallucinate "no information available" responses.
   - *Migration:* Catch the structured error and inspect `error.code`. On
     `code="gene_not_indexed"`, present the `error.close_matches` list to the user or
     retry with a corrected symbol.
     ```json
     {
       "error": {
         "code": "gene_not_indexed",
         "message": "Gene symbol 'BRCA' is not in the indexed corpus.",
         "close_matches": ["BRCA1", "BRCA2"]
       }
     }
     ```

### Added

- **`get_passage(neighbors)` parameter** -- retrieve N passages before and after the
  focal passage in a single call; reduces round-trips for sliding-window reading.
- **`get_chapter_metadata` tool / route** -- returns chapter-level metadata including
  section list with passage counts, table count, and `last_updated_date`; enables
  LLMs to plan targeted retrieval before fetching content.
- **`get_table` tool / route** -- retrieves a single table by chapter and ordinal ID,
  returning structured rows as markdown; tables were previously inaccessible to LLMs.
- **Empty-result diagnostics** -- when `search_passages` returns zero results, the
  response includes `_meta.diagnostics` with rule-based suggestions (e.g. gene not
  indexed, query too specific, try broader terms).
- **Optional `dedupe` parameter on `get_chapter_section`** -- deduplicate overlapping
  passages in `concatenated_text` when `dedupe=true`.
- **Latency hints in tool descriptions** -- MCP tool descriptions now include typical
  p50 latency ranges so LLMs can set user expectations without a probe call.
- **`passage_type` field on passage responses** -- values `narrative` or `table`;
  allows consumers to filter or format passages differently.
- **`corpus_version` in `_meta`** -- search and section responses include the corpus
  build timestamp so consumers can detect stale cached data.
- **`rapidfuzz` fuzzy matching** -- gene-symbol close-match suggestions in structured
  400 errors use edit-distance ranking for relevant alternatives.

### Fixed

- **`chapter_last_updated` always null** -- fixed NXML XPath extraction for
  `<pub-history>/<date date-type="updated">` elements; 685 of 882 chapters now carry a
  parsed `last_updated_date`.
- **`rrf_score` and `dense_rank` not wired** -- `LexicalPassageRow` now carries
  `dense_rank` and `rrf_score`; the reranker populates both fields and they are
  returned in `score_breakdown` when requested.
- **Tokenizer-leak text normalization** -- a whitespace-normalization step in the
  chunker was stripping meaningful punctuation (em-dashes, hyphens in gene symbols,
  parenthetical ranges). Fixed to preserve original casing and punctuation in stored
  chunks; existing corpora should be rebuilt to benefit.
- **Five corpus-pipeline bugs** -- surfaced by production data during Phase 7 rebuild:
  incorrect column order in asyncpg COPY, swap of `passage_type`/`table_id` in bulk
  insert, off-by-one in `chunk_index` during table interleave, missing `table_data`
  JSON serialization, and a date-format mismatch in the `last_updated_date` parser.
- **`include=score_breakdown` not propagated to debug route** -- the `/debug` passages
  route now also honours the `include` query parameter.

### Phase tags

Deployment milestones committed as annotated git tags on this branch:

| Tag | Phase | Scope |
|-----|-------|-------|
| `phase-5-ergonomics-v2` | Trust pass | `last_updated_date` fix, `rrf_score`/`dense_rank` wiring, `score_breakdown` opt-in |
| `phase-6-ergonomics-v2` | Discovery pass | `get_chapter_metadata`, `get_passage` neighbors, empty-result diagnostics, `concatenated_text` opt-in |
| `phase-7-ergonomics-v2` | Content pass | DB migration, table extraction, corpus rebuild, `get_table` route, tokenizer-leak fix |
| `phase-8-ergonomics-v2` | Polish pass | License resource, gene-symbol 400, `dedupe`, latency hints, `passage_type` exposure |

Note: `phase-7-ergonomics-v2` does not appear in the tag list at time of writing if
the rebuild was tagged locally only; check `git tag` output to confirm presence.

### Corpus stats post-rebuild

Stats from the Phase 7 full corpus rebuild against the NCBI Bookshelf GeneReviews
archive (run date: 2026-05):

| Metric | Value |
|--------|-------|
| Total passages indexed | 37,229 |
| Table passages | 7,391 |
| Narrative passages | 29,838 |
| Chapters with `last_updated_date` | 685 / 882 |
| Chapters without date | 197 / 882 |

---
