# MCP QA Polish Plan

Date: 2026-05-25

## Tasks

1. Add focused route-level regression tests for the five QA findings and confirm
   they fail for the expected reasons.
2. Normalize `get_fulltext` NBK output while preserving accepted input formats.
3. Add structured non-numeric PMID validation for `get_abstract`.
4. Add empty-result recovery affordances to `search_genereviews`.
5. Add corpus-backed minimal fallback to `get_genereview_summary` when an
   indexed chapter exists but enrichment raises `DataNotFoundError`.
6. Add a schema-level `table_id` path pattern for `get_table`.
7. Run affected tests, then `make ci-local`.
8. Commit and push to the existing `feat/phase1-correctness-and-perf` branch so
   PR #53 updates in place.
