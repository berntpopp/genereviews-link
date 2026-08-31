# Changelog

All notable changes to GeneReviews-Link are documented in this file.

## [5.1.3] - 2026-08-31

- Re-pinned the Python base image and all CI workflow dependencies to reviewed immutable revisions.

## [5.1.2] - 2026-08-10

Consolidated Dependabot maintenance release. No runtime or corpus contract
behaviour change.

### Security

- Updated locked `cryptography` to 50.0.0, closing CVE-2026-69247 without
  weakening the container vulnerability policy.

### Changed

- Widened the supported MCP SDK range to `<3.0.0` and resolved MCP 1.29.0.
- Updated all `astral-sh/setup-uv` consumers to v9.0.0 and both reusable
  container workflows to the reviewed router v0.7.4 commit.

## [5.1.1] - 2026-07-30

Consolidated Dependabot sweep. No runtime behaviour change.

### Security

- **`setuptools` 81.0.0 → 83.0.0** (`uv.lock`), closing the repo's only open Dependabot
  alert (medium). `setuptools < 83.0.0` lets a `MANIFEST.in` exclusion be bypassed when
  building an sdist: a path that differs only by Unicode NFC/NFD normalization does not
  match the exclusion pattern, so a file intended to be excluded can still be packaged.

### Fixed

- **Pinned-action version comments named tags the SHAs are not.** Three `uses:` pins
  documented a version they do not resolve to, so a reader auditing the workflows saw a
  version that was never what CI ran:
  - `actions/attest-build-provenance@43d14bc…` was commented `# v4.1.0` but is the
    annotated **`v3`** tag object (→ commit `977bb373…`, i.e. v3.0.0). Repinned to the
    real **v4.1.1** (`0f67c3f4…`), which is what the comment always claimed. v4 is a thin
    wrapper over `actions/attest`; the `subject-path` input this workflow uses is
    unchanged. (Supersedes Dependabot #118, which moved the pin to `977bb373…` — the same
    v3 commit the old SHA already dereferenced to, a functional no-op — while keeping the
    false `# v4.1.0` comment.)
  - `actions/upload-artifact@043fb46d…` was commented `# v4.6.2`; that SHA is **v7.0.1**.
  - `actions/download-artifact@3e5f45b2…` was commented `# v4.3.0`; that SHA is **v8.0.1**.

  Only the comments changed for the two artifact actions — both SHAs already are the
  current major-release tips.

### Changed

- `actions/checkout` 7.0.0 → **7.0.1** (`3d3c42e5…`); all five call sites now carry the
  precise `# v7.0.1` comment instead of a mix of `# v7` and `# v7.0.0`.
- `actions/setup-python` v6.3.0 → **v7.0.0** (`5fda3b95…`). v7 drops the `pip-install`
  input, which this repo never used.
- Left pinned to their current SHAs on purpose: the two
  `berntpopp/genefoundry-router/.github/workflows/_container-{ci,release}.yml` reusable
  workflows (`86b11f7…`). Repinning them onto current router `main` is an operator-gated
  release-control decision, not a dependency bump.

## [5.1.0] - 2026-07-15

MCP contract-hardening (issue #106). Behaviour Conformance v1 gate: CONFORMANT
(74 pass, 0 fail, 0 UNGATED). Tool surface 20,063t → 5,678t; input doc% 81 → 100.

### Fixed

- **[CRITICAL] `get_genereview_summary` resolved a gene to the WRONG GeneReviews chapter
  and stamped it with a fabricated title.** Corpus chapters carry an empty `pubmed_id`, so
  the route guard and the service's inner guard both fell through to a blind live NCBI
  E-utils lookup that took `results[0]` — a chapter merely *mentioning* the gene — and,
  with no scraped title, synthesized `"GeneReview for <GENE>"`. CFTR resolved to NBK190101
  "Pancreatitis Overview"; SCN1A to NBK1388 "Familial Hemiplegic Migraine". Resolution is
  now **always corpus-authoritative** — even with `fresh=true` (which now controls only
  whether the resolved chapter's *content* is re-fetched live). The gene is resolved to its
  **defining** chapter: the chapter where the gene is in `primary_gene_symbols`, OR is the
  chapter's sole gene, OR appears as a whole word in the chapter title. A gene that is only
  *mentioned* in a multi-gene chapter (e.g. CLDN2, which occurs only in the 13-gene
  "Pancreatitis Overview"), or is absent, returns `not_found` — never a guessed chapter and
  never a fabricated title. CFTR → NBK1250 "Cystic Fibrosis"; SCN1A → NBK1318 "SCN1A
  Seizure Disorders"; CLDN2 → not_found.
- **FastAPI list-shaped 422s were discarded.** A bad enum value / pattern-mismatched path
  param returned `invalid_input` with a bare `"HTTP 422"` naming no parameter. The error
  mapper now lifts each validation error's parameter name and message into a named
  `field_errors` entry (never echoing the caller's rejected input).
- **A syntactically-valid but nonexistent `nbk_id`** filter (e.g. `NBK999999999`) returned
  0 rows with `success:true`; it is now rejected as `not_found`.
- **`isError` was false on every error envelope.** Error frames now return
  `ToolResult(structured_content=..., is_error=True)` on both the exception path and the
  new unknown-argument path, so clients branching on `isError` see the error.
- **`error_code` harmonized to the closed six-value enum** (`internal_error` → `internal`).
- **Unknown arguments** are now rejected with `invalid_input` (never `not_found`) naming
  the tool's own valid parameters; caller-supplied argument names are never echoed.
- **D4: `gene_role` removed from `search_passages`.** It filtered on `primary_gene_symbols`,
  which is unpopulated on every current corpus, so `primary`/`mentioned` returned silently-
  empty results and the declared enum was wider than the runtime supported. Rather than ship
  a non-functional filter that lies, the parameter is removed from the tool (the repository
  keeps the capability for a future corpus re-ingest). "Which chapter is a gene about" is
  answered by `get_genereview_summary`'s fixed defining-chapter resolution.
- **A bogus `nbk_id` filter on `search_passages`** returned 0 rows with `success:true`; it
  is now rejected as `invalid_input`.
- **D5: brief-mode rows could arrive with both `text` and `snippet` null** (dense-only
  hits with no `ts_headline` fragment). Brief mode now falls back to a leading passage
  excerpt so every row carries content.

### Changed

- **Tool-surface budget:** `outputSchema` is suppressed (`output_schema=None`) and
  `dereference_schemas=False`, cutting the surface from 20,063t to 5,678t. The v1.1
  `untrusted_text` fence still rides on the wire in `structuredContent` (v1.1a amendment).
- **Schema documentation:** every required and array parameter now carries a description
  and `examples`; closed vocabularies are declared as enums. `search_passages` advertises
  `q` as required in its MCP schema so the behaviour gate can probe it — the `query` alias
  is still accepted at runtime (input validation stays lenient, the safe direction).
- Vendored the Behaviour Conformance v1 gate (`tests/conformance/behaviour.py` +
  `test_behaviour_v1.py`) and wired it into the conformance workflow.
- Re-vendored the behaviour conformance gate from genefoundry-router `56db958`
  (`docs/conformance/behaviour.py` blob `c69801687`) so live MCP contract checks
  treat not-found example probes as inconclusive and keep empty auxiliary objects from hiding counted rows.

### Notes

- **D2 (multi-level table headers) is already fixed in the parser** and covered by
  `test_extract_table_flattens_nested_headers_to_match_rows`; the deployed corpus predates
  that parser, so it resolves on the next corpus re-ingest (no code change).
- **D3 (default `rrf` buries exact-phrase matches)** is a ranking-quality issue that
  requires the real BGE embedding model to reproduce and evaluate; not addressed here.

## [5.0.6] - 2026-07-14

### Fixed

- **The NPM deployment would have lost its public hostname on the next deploy.** Nginx
  Proxy Manager forwards to a **container name** on the shared network — the live proxy
  host emits `proxy_pass http://genereview_link_server:8000;`. The `container_name` keys
  (`genereview_link_server`, `genereview_link_postgres`) were dropped from
  `docker/docker-compose.yml` when the corpus-restore sidecar landed (#97) and nothing
  restored them, so the deployed chain (`docker-compose.yml -f docker-compose.prod.yml -f
  docker-compose.npm.yml`) rendered no `container_name` at all. Compose would have
  auto-named the container `genereviews-link-genereview-link-1`, NPM could not have
  resolved it, and `genereviews-link.genefoundry.org` would have started returning 502 the
  moment the server pulled this compose. `docker-compose.npm.yml` now pins both names for
  the topology that depends on them.
- `.env.docker.example` defined `GENEREVIEW_LINK_IMAGE` **twice**, with two different
  placeholder digests; the second silently won. Consolidated into one documented entry.

## [5.0.5] - 2026-07-13

### Fixed

- Release evidence now states the data contract this repository actually declares. The
  reusable release workflow hardcoded `--contract data-independent` and
  `data_requirements: {"mode":"none"}`, so the signed release manifest claimed the image
  binds to no data -- while `container-release.json` declares `data-bound` with the pinned
  immutable corpus artifact `corpus-data-2026-07-13-r1`
  (`sha256:4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc`). Re-pinned
  the container CI and release callers to the corrected standard revision
  (`86b11f7ed062ed84dfddcbd309e34da88f3dae5b`), which reads the contract and the data
  identity from `container-release.json`.
- This also activates `_require_data_binding`, which returned early for a
  `data-independent` contract. The release now asserts that the captured data identity
  equals the pinned `data.release_tag` and `data.digest`, instead of silently skipping the
  strongest assertion in the evidence chain.

## [5.0.4] - 2026-07-13

### Changed

- **The corpus is now declared and implemented as a `restored-database`, not an
  `external-reference`.** It is what the code always did -- `pg_dump -Fc` out,
  `pg_restore` in -- but it was declared as a file the server reads, and the restore ran
  inside the request-serving process, over the network, as the database owner.
- The corpus ships as an immutable, attested, **data-only** artifact
  (`corpus-data-2026-07-13-r1`,
  `sha256:4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc`). The previous
  release was a schema-bearing dump: its table of contents carried `SCHEMA`, `TABLE`,
  `INDEX`, `CONSTRAINT`, `FK CONSTRAINT` and `EXTENSION` entries, so restoring it executed
  DDL that arrived over the network. The new artifact contains **only** `TABLE DATA` for
  four named tables. Same corpus (882 chapters, 40,853 passages), different envelope.
- A new no-egress `genereview-corpus-restore` init sidecar is the only path by which
  corpus data enters PostgreSQL. It is on an internal-only network (it can reach the
  database and nothing else), proves the artifact against a digest committed in this
  repository before opening it, rejects any archive entry that is not table data for a
  named corpus table, rejects a plain-SQL script on its magic bytes, and restores as an
  unprivileged `NOSUPERUSER` role under
  `--no-owner --no-privileges --single-transaction --exit-on-error`. Schema and indexes
  come only from the reviewed in-repo migrations.
- **The serving application has no restore path at all.** It no longer downloads a bundle,
  no longer runs `pg_restore`, and no longer needs egress to a release host.
- Harden the `postgres` sidecar to the full standard: read-only rootfs, `cap_drop: [ALL]`,
  `no-new-privileges`, bounded resources and logging, no published ports, digest-pinned
  untagged image, and an internal-only network.
- Adopt the GeneFoundry router container-release standard with SHA-pinned reusable
  container CI/release callers, digest-only production image configuration, code-only
  Docker context controls, and complete OCI image labels.

### Fixed

- The production image stripped `numpy`, which `pgvector.asyncpg` imports at module import
  time. Every database connection raised `ModuleNotFoundError`, so the whole corpus path
  was dead in the production target. numpy is retained; only its bundled test-data trees
  are removed (the fleet OCI content policy denies any `data`/`corpus` path component).
- The production image stripped `genereview_link.corpus`, which the server imported at
  startup for `BGE_MODEL_NAME`/`BGE_DIM` -- the app exited on boot. Those constants moved
  to `genereview_link.retrieval.model_identity`, which the serving image ships.
- `TMPDIR` pointed at `/tmp/genereview-link`, a directory the `/tmp` tmpfs mount hides at
  runtime. It is now `/tmp`.
- `postgres` starts as its own `999:999` rather than as root. The stock entrypoint drops to
  the postgres user with `gosu`, which needs `CAP_SETUID`/`CAP_SETGID`, and chowns `PGDATA`,
  which needs `CAP_CHOWN` -- both impossible under the mandatory `cap_drop: [ALL]`, where
  the container died with "operation not permitted".

Earlier release notes are retained in [docs/CHANGELOG.md](docs/CHANGELOG.md).

## [5.0.3] - 2026-07-12

### Security

- Bound corpus-ingest download deadlines and archive expansion, including
  artifact-specific deadlines for release downloads, to prevent unbounded
  ingestion work from consuming service resources.
- Pinned all GitHub Actions used by CI and release workflows to immutable SHA
  revisions.
