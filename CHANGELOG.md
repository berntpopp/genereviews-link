# Changelog

All notable changes to GeneReviews-Link are documented in this file.

## [Unreleased]

## [5.2.4] - 2026-09-02

- **Adopted the GeneFoundry runtime data identity (`runtime-v1`).** Until now
  `container-release.json` declared `data_identity_contract: "unadopted"`: the deployment
  could not prove *which* reviewed corpus release it was serving, so the fleet controller
  had no way to activate a new one for it. `GET /health` now carries `data_available` and
  `release_identity` (`schema_version: 1`, `data_identity.{expected,actual}`, each exactly
  `{release_tag, digest}`). `expected` is the configured release
  (`CORPUS_RELEASE_TAG` + the seed digest, i.e. `container-release.json`
  `.data.release_tag`/`.data.digest`); `actual` is re-derived from what is restored, never
  from configuration.
- **The identity is written by the restore and re-checked by the server.** New control
  migration `0008_runtime_data_identity`. The no-egress init sidecar hashes the staged
  artifact itself, requires the artifact manifest's `corpus_release_id`, `corpus_version`
  and chapter/passage/embedding counts to equal the rows really in the database, and only
  then records the row — on *both* paths, including the already-restored one, where nothing
  is restored but the artifact is re-proved and re-bound, so an existing deployment adopts
  the contract on its next start without a re-restore. The server re-reads the live corpus
  version and counts at startup before republishing the row, so a corpus swapped underneath
  a running deployment stops matching and `data_available` goes false.
- **New read-only probe:** `python -m genereview_link.data_probe` prints exactly
  `{"data_schema_version", "record_count", "query_result_sha256"}` from a `READ ONLY`,
  `REPEATABLE READ` transaction over `DATABASE_URL` — the controller execs it in the app
  container to observe the data independently of what the deployment claims.
  `container-release.json` now declares
  `data.schema_compatibility: ["0007_embedding_run_identity"]`.
- A corpus that is serving while its identity is absent or disagrees now reports
  `status: "degraded"` — the same failure class as the corpus that sat frozen while
  `/health` stayed green (#145).
- `docker/docker-compose.yml` defaults `CORPUS_RELEASE_TAG` to the pinned
  `container-release.json` release (a guard test fails if the two ever disagree) and passes
  `CORPUS_RELEASE_TAG` / `CORPUS_BUNDLE_SHA256` / `CORPUS_DUMP_SHA256` to the *server* as
  well, so an unchanged server `.env.docker` renders the reviewed identity rather than an
  empty one. The server still has no restore path and never reads the seed.
- Re-pinned the reusable container CI/release workflows to `genefoundry-router` v0.8.6
  (`3d3cc20`), whose release config accepts `data.schema_compatibility`.
- **Forward note for the next *direct* (manifest-v3) corpus release:** the readiness record
  compares applied migrations against the manifest's, so a bundle must be built by an app
  build that has the same control-migration set. Build the next data release with 5.2.4 or
  later; a manifest-v3 bundle built by <= 5.2.3 would fail readiness under 5.2.4. The
  current legacy (manifest-v2) pin `corpus-data-2026-07-13-r1` is unaffected and unchanged.

## [5.2.3] - 2026-09-02

- **No reader of a `jsonb` column could see its contents.** asyncpg has no built-in jsonb
  codec and nothing here registers one, so `source_capture` and the ingest/embedding
  `provenance` records arrive as `str`. Every corpus reader tested
  `isinstance(value, dict)` and refused the row it had just been handed — so a freshly
  ingested, fully embedded 890-chapter corpus still failed `bundle validate` with "active
  corpus lacks retained source and computation-run identity" while the row was complete and
  correct. `bundle_validation`, `bundle_metadata`, `semantic_identity` and
  `computation_runs` now decode through one helper that leaves already-decoded objects
  untouched and refuses anything that is not a JSON object. The write path is unchanged:
  it still binds canonical JSON text with an explicit `::jsonb` cast, so no stored bytes
  change shape.
- `load_active_computation` also returned the raw text for `provenance`, so even a caller
  that got past the check would have put a string into the release manifest where the
  verifier requires an object.

## [5.2.2] - 2026-09-02

Two more refusals that only the *real* upstream bytes trigger, both found running the
newly unblocked pipeline against live NCBI data.

- **NCBI's litarch index is not canonical UTF-8, and the capture reader demanded that it
  be.** Four rows out of 9508 — NTP technical reports and Spanish-language WHO documents —
  carry latin-1 bytes, so `load_offline_capture` refused every real capture with
  "file_list.csv is not canonical UTF-8" over rows that have nothing to do with
  GeneReviews. Rows are now decoded individually and undecodable ones skipped; the raw
  bytes are still digest-bound, and the "exactly one canonical NBK1116 row" rule still
  governs — so an undecodable GeneReviews row would be skipped and fail closed rather than
  slip through. The snapshot writer and the capture reader now share one row decoder and
  cannot disagree about what a listing contains.
- **Offline ingest died at stage 0 on any real capture.** A capture's side-data entry
  carries `{url, sha256, size_bytes}` because the capture attests where the bytes came
  from; the corpus-version row and the release manifest record digest and size only, and
  `validate_source_identity` demands exactly those two keys. Handing it a capture entry
  verbatim raised "side_data GRtitle_shortname_NBKid.txt must contain exactly sha256 and
  size_bytes" before a row was ever written. The pipeline now projects capture entries onto
  the stored identity shape, and refuses an entry that lacks a digest identity.

## [5.2.1] - 2026-09-02

- **`archive_content_identities` did not finish on the real archive.** It hashed members in
  sorted name order over a `tarfile` opened as `r:gz`, and the GeneReviews archive's own
  member order is not sorted order — so every backwards seek rewound the gzip stream and
  re-inflated it from byte zero. Measured on the live 636 MB / 2925-member archive: 386 MB/s
  of re-inflation for over an hour without finishing, and it is called up to three times per
  build (snapshot fetch, snapshot verify, ingest). Inflating once into a seekable temporary
  file makes the same reads O(1): the same two digests now take **2.5 seconds**. Every
  bound, refusal and digest is unchanged, and a test pins the digests against the plainly
  written sorted-order definition as well as the single-inflation property.
- The decompression cap is now enforced *during* inflation rather than only on the
  regular-member total, so a gzip bomb dies before it can fill the decompression target.

## [5.2.0] - 2026-09-02

- **Made the corpus pipeline bootstrappable.** `ingest` required a prior manifest/seal
  pair chaining to a release built under the current scheme (`manifest_version == "3"`,
  seal `genereviews-local-handoff-v1`), and no such release has ever existed — so the
  first build under that scheme was unreachable by construction and no corpus could be
  produced at all (#147). `ingest --genesis` is the explicit first build of a chain: no
  prior pair, `genesis: true` / `prior_artifact: null` in the capture, `genesis: true` /
  `prior: null` in the emitted seal manifest, and `delta_from_prior` reporting the whole
  corpus as added. A missing prior *without* `--genesis` is still refused — the genesis
  case is declared, never inferred.
- **Fixed the chained path too.** `content_identity` never carried `chapter_count`, but a
  capture's `prior_artifact` must, and the two are compared field-for-field — so every
  chained ingest against a *real* manifest was unprovable as well. The identity now
  records it, and a test chains a second build off a genesis release end to end.
- **Added `genereview-link snapshot`**: acquisition of the offline source set `ingest`
  consumes, assembled from NCBI's litarch listing/archive and the three GeneReviews
  side-data files into exactly that layout, with a `snapshot-manifest.json` recording
  every fetched URL, digest, byte count and upstream `last_updated`. Re-runs resume rather
  than re-download the ~600 MB archive; requests are paced with NCBI's published courtesy
  interval; `--acknowledge-terms` is required before any byte is written and is recorded
  next to the files. Acquisition is not redistribution: the publication rights gate
  (`rights.py`, the dated determination in #27) is unchanged.
- Reconciled `docs/data.md`, which described two incompatible release schemes. It now
  documents one flow — `snapshot` → `ingest --genesis` (or chained) → `embed` →
  `bundle publish-local` → `seal-handoff` → `corpus-data-release.yml` — plus the real
  eight inputs and dispatcher of `verify-corpus-bundle.yml` (it verifies an *already
  published* release and is dispatched by the publisher, not by hand), the protected
  configuration both CI paths still need, and one sentence marking the pre-manifest-v3
  `corpus-bundle.tar.gz` shape historical.
- `BUILD_LOCAL=true` is inert and now says so. It named a boot-time live ingest that no
  longer exists, so the branch behind it reached `run_full_ingest(pool)` with no source
  set and raised a `ValueError` the bootstrap's error handler did not catch — killing
  startup with a message that never mentioned the flag. It now logs an explicit error and
  degrades exactly as an empty database does.

## [5.1.8] - 2026-09-02

- Surfaced the active corpus's freshness in `GET /health`. The corpus-refresh scheduler
  itself was already fixed in 5.1.7 (the advisory-lock-guarded release watcher replaced
  the bare `pass  # implementation extends Task 6.3`), but a frozen corpus still reported
  plain `healthy` indefinitely because nothing checked its age. `/health` now reports
  `corpus.data_as_of` (the active corpus's `ingest_finished_at`, restored verbatim from
  the release bundle) and goes `degraded` once `CORPUS_MAX_AGE_DAYS` (new setting, default
  90) is exceeded.
- Added a repo-wide guard test for the exact shape of the original bug — a `pass`
  statement whose own trailing comment defers its implementation — so the next one fails a
  test instead of shipping silently.
- Documented the fleet deploy contract in `AGENTS.md`: the compose file the external
  controller deploys vs. the one the release gate validates, the numeric `user: "999:999"`
  split between them, and the release checklist this repo's own tests enforce.

## [5.1.7] - 2026-09-01

- Refused placeholder SHA-256 values as corpus bundle identities. Production ran with
  `CORPUS_BUNDLE_SHA256` set to 64 zeroes, inherited from this repository's own compose
  default: a syntactically valid digest that made an entirely unpinned deployment classify
  as a valid "legacy" identity, so the restore sidecar exited 0 on every deploy while
  verifying nothing. Placeholders are now refused by identity, the compose default is gone,
  and a repo-wide test prevents any tracked file shipping one again.
- Turned a missing corpus seed artifact into a restore error rather than a bare
  `FileNotFoundError` traceback.
- Failed closed on a stub embedding provider. `GENEREVIEW_EAGER_LOAD_BGE` reads as a
  loading strategy but selects real-vs-stub embeddings, and being unset in production meant
  query vectors were hashes rather than BGE embeddings. Fusing them into reciprocal-rank
  fusion displaced correct lexical hits with unrelated passages while every response still
  reported `dense_model_id: "BAAI/bge-small-en-v1.5"`. Production now refuses to start on
  the stub without `GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true`, the dense path is disabled
  whenever the live provider is not the corpus's model, `dense_model_id` reports what was
  actually loaded, a query/corpus model mismatch is refused, and `/health` reports
  `degraded`. Added `GENEREVIEW_EMBEDDING_PROVIDER` as the honest name for the choice.
- Logged the installed package version instead of a hardcoded `3.0.0`.
- Documented that `BUNDLE_URL` has been inert since the no-egress restore sidecar landed.
- Made real semantic search actually possible in the serving container. The image could not
  carry PyTorch (526 MB wheel, over the fleet OCI content policy's 64 MiB per-file ceiling),
  so it now runs the same pinned BGE weights through ONNX Runtime (largest file 30 MB,
  +26 MB to the image) and the 127 MiB weights arrive as a digest-pinned artifact staged
  beside the corpus and materialised once into a volume by the existing no-egress init
  sidecar, mounted read-only by the server and re-verified before load. ONNX parity with
  the sentence-transformers path that built the corpus is measured, not assumed: minimum
  cosine 0.999999999796, maximum per-dimension delta 1.75e-07. Production now defaults to
  the real model instead of requiring an opt-in to the stub.
- Added `genereview-link model stage` and `genereview-link init`; the init sidecar now
  materialises the model and then restores the corpus in one shell-free argv.
- Declared explicit numeric `user: "999:999"` and a `volumes:` key on every deployed
  service, so the rendered production compose satisfies the fleet deployment gate's
  `_APP_REQUIRED` projection.
- Replaced the release watcher's silent no-op. `AUTO_PULL_RELEASES` gated a branch that was
  a bare `pass`, so nothing ever wrote `public.genereview_refresh_log` and the corpus sat at
  2026-05-12 unnoticed. Setting it is now a startup error; `RELEASE_WATCHER_ENABLED` records
  a `current` / `stale` / `no-active-corpus` / `upstream-unavailable` observation each hour.

## [5.1.6] - 2026-08-31

- Bound every corpus bundle to the exact upstream listing path, archive digest and size, and all
  three GeneReviews side-data digests and sizes recorded during ingest.
- Closed handoff copy races and made the sealed file-mode manifest match the immutable object.
- Hardened the rights-gated publisher around exact numeric GitHub release IDs, draft-inclusive
  inventory, exact tag/source ownership, bounded asset verification, and immutable promotion.
- Restored sealed-object permissions after artifact transfer and removed unsealed publisher
  dependencies from the credentialed job. The repository owner supplied an affirmative
  redistribution determination dated 2026-09-01; publication remains bound to its exact durable
  evidence, source/artifact digests, and required University of Washington attribution.
- Retained exact attested build subjects through an immutable owner-materialization artifact and
  required an active no-bypass immutable-tag ruleset before any corpus draft or asset mutation.

## [5.1.5] - 2026-08-31

- Preserved the current NCBI GeneReviews corpus when a malformed trailing `colspan`
  overflows the declared table header, while retaining strict rejection for real row-width
  mismatches. The fresh 2026-08-31 source now parses all 890 mapped chapters.
- Reset stale staging migration records after an interrupted ingest so a safe retry recreates
  every staging table before writing and still leaves the active corpus untouched until swap.

## [5.1.4] - 2026-08-31

- Pinned the data-release provenance attestation action to its reviewed immutable v4.2.2 revision.
  This is workflow source maintenance only; it does not publish or alter corpus data.

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
