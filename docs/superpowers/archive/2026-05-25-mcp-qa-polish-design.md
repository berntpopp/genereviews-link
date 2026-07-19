# MCP QA Polish Design

> Historical record

Date: 2026-05-25
Status: Completed in PR #53 (`39af970`, follow-up `15ffbec`).

## Goal

Close the expert QA gaps that keep the public GeneReview-Link MCP surface from a
10/10 experience while preserving the existing canonical corpus-backed workflow.

## Scope

The change targets five externally visible inconsistencies:

1. `get_fulltext` must return canonical `NBK...` identifiers, even when the live
   Bookshelf scraper returns bare digits.
2. `get_genereview_summary` must degrade to a minimal corpus-backed summary when
   an indexed chapter is known but optional enrichment fails.
3. `get_abstract` must reject non-numeric PubMed IDs before calling live NCBI.
4. `search_genereviews` empty successful results must include agent recovery
   affordances.
5. `get_table` must expose a schema-level `table_id` pattern in OpenAPI/MCP
   tool metadata.

Out of scope: ranking behavior, scraper selector changes, dependency changes,
and public hosted destructive tools.

## Design

Use existing route and model seams instead of adding a new orchestration layer.

- `FullTextData.nbk_id`: normalize the route output with a small helper that
  accepts either `1247` or `NBK1247` and emits canonical `NBK1247`.
- `GeneReview` summary resilience: when the route has already resolved an
  indexed chapter, catch `DataNotFoundError` from enrichment and synthesize a
  minimal `GeneReview` from the corpus chapter row. The response remains
  corpus-version stamped and avoids live resolver fragility.
- `get_abstract`: add structured `invalid_pubmed_id` with HTTP 422 and validate
  `pubmed_id.isdigit()` before `EutilsClient.fetch_abstract`.
- `SearchResult`: add an optional `recovery_hint` that serializes only when set.
  On empty results, populate it and `_meta.next_commands` with a
  `search_passages(gene=..., q=...)` retry.
- `get_table`: add `Path(pattern=...)` for table IDs that permits existing slug
  forms such as `t5` and `brca1.molgen.TA`.

## Testing

Each behavior gets a route-level regression test that first fails against the
pre-fix implementation. Verification uses affected route/service tests followed
by `make ci-local`.

## Risks

The only response shape expansion is `SearchResult.recovery_hint` on empty
successes. It is omitted when null to avoid token and schema noise on normal
hits.
