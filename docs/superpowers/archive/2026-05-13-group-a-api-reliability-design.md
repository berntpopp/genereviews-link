# Group A API Reliability And LLM Recovery Design

**Date:** 2026-05-13
**Issues:** #41, #42, #35, #47
**Scope:** One remediation phase focused on orchestration entry points.

## Goal

Make the public orchestration tools reliable, diagnosable, and self-routing for
LLM clients:

- `search_genereviews`
- `get_genereview_summary`
- `get_abstract`
- `get_fulltext`
- `get_links`

The key behavior change is repository-first lookup when the indexed corpus can
answer the request, with live NCBI calls retained only for `fresh=true` and
true fallback cases.

## Problems Addressed

**#41 PMID-to-NBK resolver bug.** `get_genereview_summary(gene_symbol="BRCA1")`
can fail because the service resolves PubMed to Bookshelf through live NCBI
`elink` even though the local corpus already has `pubmed_id -> nbk_id`. The
repository already exposes `get_chapter_by_gene()` and `get_chapter_by_pmid()`;
the route/service layer does not use them.

**#42 generic HTTP 500 errors.** Orchestration routes return string-only
`HTTPException` details. Passage and chapter routes already use
`StructuredHTTPException` with `code`, `recovery_hint`, `field_errors`, and
`next_commands`; orchestration routes should return the same recoverable shape.

**#35 inconsistent `corpus_version`.** Indexed endpoints put the active corpus
version in `_meta.corpus_version`, but default abstract/fulltext/summary/search
orchestration calls leave top-level `corpus_version` null unless `fresh=true`.

**#47 missing fallback documentation.** Entry-point descriptions and usage docs
do not say which tools depend on PMID/NBK resolution, which fallback to use, or
what value the wrapper adds over direct NCBI E-utils.

## Non-Goals

- No schema migration.
- No response-breaking rename of existing top-level `corpus_version` fields.
- No broad rewrite of the scraper.
- No Group B size guardrail changes beyond documentation needed for Group A.
- No Group C reference/table cleanup.

## Design

### Repository-first orchestration

Add a request-scoped repository dependency to the orchestration routes that can
benefit from the indexed corpus:

- `GET /search/{gene_symbol}`:
  - default path uses `repo.get_chapter_by_gene(gene_symbol.upper())`;
  - returns a `SearchResult` containing the indexed PubMed ID when found;
  - if no indexed chapter is found, falls back to the existing live
    `EutilsClient.search_genereviews()`;
  - `fresh=true` bypasses the repository and uses the live client.

- `GET /genereview/{gene_symbol}`:
  - default path resolves the gene through `repo.get_chapter_by_gene()`;
  - service builds the Bookshelf URL directly from the indexed NBK ID;
  - optional abstract/links/fulltext enrichment may still use the live client;
  - `fresh=true` preserves current live behavior.

- `GET /abstract/{pubmed_id}` and `GET /links/{pubmed_id}`:
  - continue to fetch live PubMed/E-utils content;
  - when the PubMed ID is present in the indexed corpus, attach the active
    corpus version to both top-level `corpus_version` and `_meta.corpus_version`;
  - `fresh=true` sets both version fields to `live:<iso timestamp>`.

- `GET /fulltext/{nbk_id}`:
  - still scrapes live Bookshelf content because Group A does not implement
    corpus-backed whole-chapter reconstruction;
  - default responses for indexed NBK IDs carry the active corpus version;
  - `fresh=true` sets both version fields to `live:<iso timestamp>`.

The local repository remains optional at app level. If the repository is
unavailable, routes fall back to current live behavior where possible. A missing
repository must not convert working live routes into 503s.

### Service boundary

Extend `GeneReviewService` to accept an optional `GeneReviewRepository` and add
a repository-first code path for `get_genereview_comprehensive()`:

- resolve chapter by gene through the repository;
- derive `pubmed_id` and `book_url` from the indexed chapter;
- avoid `get_book_url_from_pmid()` when NBK ID is known;
- fallback to the existing live implementation if the repository has no match
  or `fresh=true`.

Fix the live PubMed-to-Bookshelf fallback while touching this path:

- use NCBI `elink` neighbor-style lookup rather than `cmd=prlinks`;
- accept linkset DB names containing `"book"` instead of only `dbto=="books"`.

### Structured errors

Add a small helper module for orchestration error construction, for example
`genereview_link/api/orchestration_errors.py`. It should map common failures to
structured payloads:

- `gene_not_found`
- `pmid_resolver_failed`
- `chapter_not_in_corpus`
- `upstream_ncbi_unavailable`
- `upstream_ncbi_timeout`
- `internal_error`

Every public orchestration route should raise `StructuredHTTPException` for
expected 4xx and 5xx failures. Each payload must include:

- `code`
- `message`
- `recovery_hint`
- `next_commands` when a caller can try a better tool
- `field_errors` when the input itself is invalid

For `search_genereviews` and `get_genereview_summary`, the primary fallback
command is:

```json
{"tool": "search_passages", "arguments": {"gene": "<GENE>", "q": "<GENE>"}}
```

When a PubMed ID cannot be resolved to an indexed chapter, the fallback command
is:

```json
{"tool": "get_chapter_metadata", "arguments": {"nbk_id": "<known NBK if available>"}}
```

Only include commands with arguments the server actually knows.

### Corpus version consistency

Add a shared helper to stamp response models:

- if `fresh=true`: top-level `corpus_version = "live:<iso>"` and
  `_meta.corpus_version = same`;
- if an indexed chapter is involved and `app.state.corpus_version` is set:
  top-level `corpus_version = app.state.corpus_version` and
  `_meta.corpus_version = same`;
- otherwise leave both as `None`.

This preserves existing fields and makes `_meta` consistent with newer routes.

### Documentation and tool descriptions

Update:

- route `summary`/`description` strings for the five orchestration tools;
- `genereview_link/api/resources/usage.py`;
- `genereview_link/mcp/prompts.py` if prompt wording mentions the old pipeline.

The documentation must say:

- which tools use the indexed corpus by default;
- which tools may still call live NCBI;
- how `fresh=true` changes behavior and versioning;
- fallback patterns from orchestration tools to `search_passages`,
  `get_chapter_metadata`, `get_abstract`, or `get_links`;
- the value-add over raw E-utils: corpus-version stamping, normalization, and
  structured recovery hints.

## API Compatibility

The existing response models remain valid. Group A only populates fields that
already exist (`corpus_version`, `_meta`) and changes error details from strings
to structured JSON for expected failures. Clients that display error strings may
need to read `detail.message`; this is acceptable because the existing 500/404
shape was not actionable.

## Testing Strategy

Add focused unit/route tests:

- `search_genereviews` returns indexed PubMed ID without live E-utils when the
  repository has a gene hit.
- `get_genereview_summary` builds `https://www.ncbi.nlm.nih.gov/books/NBK1247/`
  from the indexed chapter and does not call `get_book_url_from_pmid()`.
- default abstract/fulltext/links/summary/search responses stamp
  `corpus_version` from `app.state.corpus_version` when indexed context exists.
- `fresh=true` stamps `live:<iso>` and uses the live code path.
- route failures contain `code`, `recovery_hint`, and relevant `next_commands`.
- live resolver parsing accepts `pubmed_books` / `pubmed_books_refs` style
  linksets.

Run `make ci-local` before claiming the phase complete.

## Acceptance

- `get_genereview_summary("BRCA1")` resolves to NBK1247 from the local corpus
  with no PubMed-to-Bookshelf network resolver call when the chapter is indexed.
- `search_genereviews("BRCA1")` returns a local corpus result by default and
  still supports `fresh=true`.
- Expected failures from all public orchestration routes return structured
  diagnostics with actionable fallbacks.
- Default indexed responses from search/abstract/fulltext/links/summary carry
  the active corpus version consistently.
- MCP usage guidance and route descriptions agree on the canonical fallback
  pipeline.
