# Precomputed Corpus Bundle Release Workflow - Design Spec

**Date:** 2026-05-12
**Issue:** https://github.com/berntpopp/genereviews-link/issues/27
**Author:** Codex, in collaboration with project maintainer

---

## Goal

Make production Docker deployments start from a precomputed GeneReviews corpus bundle instead of spending first boot on ingest and embedding backfill.

The expensive build happens on the maintainer workstation with the local RTX 5090. The result is published as a GitHub Release asset. Docker pulls that release asset on first boot, verifies it, restores Postgres with `pg_restore`, and starts serving immediately.

## Current State

GeneReview-Link already has most of the consumer-side machinery:

- `BUNDLE_URL` restore mode in `genereview_link/server_manager.py`.
- `BUNDLE_URL=latest` GitHub Release resolution in `genereview_link/ingest/github_release.py`.
- Bundle packing in `genereview_link/corpus/bundle.py`.
- `genereview-link bundle build` and `make bundle`.
- Docker docs that mention bundle restore mode.
- A scheduled `.github/workflows/build-corpus.yml` that currently tries to build the corpus in GitHub Actions.

The current database status from the issue discussion:

| Metric | Value |
| --- | ---: |
| Chapters | 882 |
| Passages | 40,853 |
| Embeddings | 13,056 / 40,853 |
| Database size | 158 MB |
| Main data schema | `genereview` |
| API container | Running healthy |
| Embedding backfill | Running, CPU-bound |

The gap is not "can the app restore a bundle?" The gap is a reliable producer workflow, manifest/version policy, promotion policy, and Docker default that make release bundles the normal deployment path.

## Phentrieve Comparison

Phentrieve's workflow is the right precedent:

- Build precomputed data bundles.
- Publish them as GitHub Release assets.
- Let Docker use a bundle URL by default.
- Keep a local/custom build path for maintainers and advanced users.
- Store manifest and checksums inside the bundle.

GeneReview-Link should copy the deployment shape, but not the compute placement. Phentrieve can build HPO/index bundles in GitHub Actions. GeneReview-Link embedding backfill is currently too slow on CPU, and the maintainer has a local RTX 5090 with 32 GB VRAM. The canonical corpus builder should therefore be local CUDA, with GitHub Releases used for distribution and CI used for verification.

## Non-Goals

- Do not make GitHub Actions the canonical corpus builder.
- Do not require Docker production hosts to run `BUILD_LOCAL=true`.
- Do not replace Postgres or pgvector bundle format.
- Do not support multiple embedding models in this pass.
- Do not introduce destructive remote cache operations.
- Do not mutate or replace existing release assets in place.

## Architecture

```
RTX 5090 workstation
  -> clean Postgres/pgvector database
  -> db migrate
  -> ingest GeneReviews corpus from NCBI archive
  -> embed all passages on CUDA
  -> build HNSW index
  -> validate corpus completeness and smoke search
  -> build tar.gz bundle + sha256 + manifest
  -> upload draft/prerelease GitHub Release
  -> CI restore verification
  -> promote stable release

Production Docker
  -> empty Postgres volume
  -> BUNDLE_URL=latest or pinned URL
  -> download GitHub Release asset
  -> verify sibling sha256 and manifest checksums
  -> pg_restore corpus.dump
  -> serve without local ingest/backfill
```

The producer workflow is maintainer-operated and explicit. The consumer workflow is automatic on first Docker boot.

## Bundle Format

The current bundle shape remains:

```
genereview-corpus-<release-id>-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
├── manifest.json
├── corpus.dump
└── sidedata/
    ├── GRtitle_shortname_NBKid.txt
    ├── NBKid_shortname_genesymbol.txt
    └── NBKid_shortname_OMIM.txt
```

`genereview-corpus-...tar.gz.sha256` is uploaded as a sibling release asset. The restore path continues to fetch and verify that sibling checksum before extraction.

## Manifest Requirements

`manifest.json` must be sufficient to decide compatibility and audit provenance. It should include:

- `manifest_version`.
- `bundle_format`.
- `corpus_release_id`, for example `2026-05-12-r1`.
- `corpus_version` from `public.genereview_corpus_version`.
- `app_git_sha`.
- `app_version` if available.
- `schema_migrations.control`.
- `schema_migrations.data`.
- `postgres.major_version`.
- `postgres.pgvector_version`.
- `chapter_count`.
- `passage_count`.
- `embedding.count`.
- `embedding.expected_count`.
- `embedding.model_name`.
- `embedding.dimension`.
- `embedding.distance_metric`.
- `embedding.active_table`.
- `embedding.device_requested`.
- `embedding.device_resolved`.
- `embedding.cuda_available`.
- `embedding.build_batch_size`.
- `hnsw.index_name`.
- `hnsw.exists`.
- `source.file_list_url`.
- `source.file_list_etag` when available.
- `source.tarball_sha256`.
- `source.tarball_size_bytes`.
- `source.tarball_last_updated`.
- `validation.status`.
- `validation.smoke_queries`.
- `created_at`.
- `created_by`.
- `license`.
- `checksums`.

The current `BundleManifest` can be extended in place. Unknown future manifest fields should be ignored by older code unless a required compatibility check fails.

## Versioning Policy

Corpus bundle releases are data releases, separate from Python/package releases.

Stable release tag:

```text
corpus-YYYY-MM-DD-rN
```

Example:

```text
corpus-2026-05-12-r1
```

Asset name:

```text
genereview-corpus-YYYY-MM-DD-rN-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
```

Rules:

- Tags are immutable once promoted.
- Assets are not replaced in place after promotion.
- During rapid schema/ranking changes, publish `r1`, `r2`, `r3` instead of replacing an asset.
- Use draft/prerelease releases for validation candidates.
- Only validated, non-prerelease releases are eligible for `BUNDLE_URL=latest`.
- Keep older corpus releases so pinned Docker deployments remain reproducible.
- If restore compatibility breaks, publish a new corpus release and document the minimum app version in release notes and manifest.

This intentionally favors boring immutable releases over a moving "recommended" alias while the project is still changing quickly.

## Latest Resolution

`BUNDLE_URL=latest` should resolve the latest stable GitHub Release containing a `genereview-corpus-*.tar.gz` asset.

The current implementation uses GitHub's latest release endpoint. That is acceptable if all prerelease/draft validation candidates stay unpromoted until passing. The spec requires that validation candidates are not published as normal stable releases.

Pinned URLs remain the production recommendation for reproducibility:

```text
BUNDLE_URL=https://github.com/berntpopp/genereviews-link/releases/download/corpus-2026-05-12-r1/genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
```

`BUNDLE_URL=latest` is the convenience default after the first promoted bundle exists.

## Local RTX Builder

Add a maintainer command that orchestrates the full local build:

```bash
uv run genereview-link bundle publish-local \
  --release-id 2026-05-12-r1 \
  --device cuda \
  --repo berntpopp/genereviews-link \
  --draft
```

The command should:

1. Confirm CUDA is available when `--device cuda`.
2. Run control and data migrations.
3. Run full ingest.
4. Backfill embeddings with `INGEST_EMBED_DEVICE=cuda`.
5. Build the HNSW index.
6. Validate counts.
7. Build the bundle and `.sha256`.
8. Optionally upload assets to a GitHub Release through `gh`.

The command should be resumable at the coarse stage level by checking database state. It should fail rather than publish if embeddings are incomplete.

## Validation Gates

A bundle cannot be uploaded or promoted unless these checks pass:

- Active corpus version exists.
- Chapter count is at least 880.
- Passage count is at least 40,000.
- Embedding count equals passage count for the active model.
- HNSW index exists.
- `public.genereview_active_embedding` points at the bundled embedding table/model.
- Bundle tarball checksum is written.
- Manifest checksums match every bundled file.
- Restore test succeeds into a disposable Postgres database.
- Smoke search returns at least one passage for `BRCA1 risk-reducing mastectomy`.
- Smoke search returns corpus metadata with the active corpus version.

The initial thresholds are intentionally conservative. They catch obvious partial builds without baking exact passage counts into code while chunking continues to evolve.

## GitHub CI Role

GitHub Actions should not do the expensive ingest/embed build. CI should verify release candidates:

- Manual workflow input: release tag or asset URL.
- Download bundle and `.sha256`.
- Verify tarball checksum.
- Extract and verify manifest checksums.
- Start `pgvector/pgvector:0.8.2-pg18`.
- Restore `corpus.dump`.
- Run a small SQL validation query set.
- Run a small API/search smoke if practical.
- Emit a verification summary.

The existing `.github/workflows/build-corpus.yml` should be replaced or repurposed into `verify-corpus-bundle.yml`. The old scheduled CPU builder should be disabled to avoid expensive slow builds and conflicting data releases.

## Docker Behavior

Docker production deployment should pull the release bundle from GitHub:

- `.env.docker.example` should recommend `BUNDLE_URL=latest` after the first promoted bundle exists.
- Docs should show both `BUNDLE_URL=latest` and a pinned release URL.
- `BUILD_LOCAL=false` remains the production default.
- If `BUNDLE_URL` is unset and the database is empty, the app may start degraded as today, with corpus-backed search returning 503.
- If `BUNDLE_URL` is set and restore fails, startup should log the failure clearly and keep the current graceful degradation behavior unless the operator opts into strict startup later.

Docker must not run ingest/backfill by default. Ingest/backfill is only for `BUILD_LOCAL=true` and local maintainer workflows.

## Error Handling

Producer-side failures:

- Missing CUDA with `--device cuda`: fail before ingest.
- Incomplete embeddings: fail before bundle build.
- Missing HNSW index: fail before upload.
- Missing GitHub CLI or auth for upload: keep local files and print next command.
- Existing release tag: fail unless `--append-assets` is explicitly provided for draft/prerelease candidates.

Consumer-side failures:

- Missing `.sha256`: fail restore.
- Tarball SHA mismatch: delete downloaded file and fail restore.
- Manifest checksum mismatch: fail restore.
- `pg_restore` failure: fail restore and log command stderr.
- Existing active corpus: skip restore.

## Testing Strategy

Unit tests:

- Manifest generation includes migration versions and count fields.
- Release ID and asset naming validation.
- CUDA preflight reports clear failure when CUDA is unavailable.
- `resolve_latest` ignores non-corpus assets.

Integration tests:

- Existing bundle round trip remains green.
- Build a mini bundle, restore it into a disposable database, validate counts.
- Verify command rejects an incomplete embedding table.
- Verify command accepts a complete mini corpus.

Workflow tests:

- Static workflow structure test for `verify-corpus-bundle.yml`.
- Docker compose config test ensures `BUNDLE_URL` is passed into the app container.

Manual release checklist:

1. Confirm local CUDA: `nvidia-smi` and `torch.cuda.is_available()`.
2. Run local publish command with `--draft`.
3. Run CI verification workflow on the draft release.
4. Promote release after CI passes.
5. Test Docker with `BUNDLE_URL=latest` on a fresh volume.

## Open Decisions

Resolved:

- Canonical builder is local RTX 5090, not GitHub Actions.
- Docker consumes GitHub Release assets.
- Keep Postgres custom dump inside tar.gz.
- Use immutable `corpus-YYYY-MM-DD-rN` data releases.

Deferred:

- Whether to add a strict startup mode that exits if bundle restore fails.
- Whether to mirror bundles to another artifact host if GitHub Release asset size becomes a problem.
- Whether to add a separate "recommended" release pointer after schema churn slows down.

## Acceptance Criteria

- A maintainer can build a complete corpus and embeddings locally with CUDA.
- The generated bundle contains complete embeddings and a manifest with provenance and compatibility fields.
- The bundle is uploaded as a GitHub Release asset with a sibling `.sha256`.
- CI can verify a release asset without rebuilding the corpus.
- A fresh Docker deployment with `BUNDLE_URL=latest` restores the release bundle and serves corpus-backed search without local ingest/backfill.
- Documentation clearly distinguishes local build, release restore, and external database modes.
