# Issue #27: Corpus Release Bundle CI Workflow — Implementation Plan

> Historical record

> **Design-only deliverable.** This plan cannot be auto-merged or validated in
> a sandbox: it requires real NCBI ingest (~40 k passages), Hugging Face model
> download (~30 MB), and `contents: write` GitHub permissions. Implement it on
> the real repository after design review.

**Goal:** Replace the stub `build-corpus.yml` (currently a disabled
placeholder) with a working GitHub Actions workflow that populates a Postgres
corpus from NCBI GeneReviews, builds a `pg_dump` bundle, uploads it as a
GitHub Release asset, and makes Docker first-start fast by defaulting
`BUNDLE_URL=latest`.

**Effort: M** — most plumbing already exists; the gap is wiring it together
inside a CI workflow with enough runner resources.

---

## 1. Current State

### What exists

| Component | File | Key lines |
|---|---|---|
| `bundle build` CLI | `genereview_link/cli.py` | L266–L358 — dumps DB, computes sha256, writes manifest + tarball |
| `bundle publish-local` CLI | `genereview_link/cli.py` | L398–L458 — ingest → embed → bundle → `gh release create` |
| `BundleManifest` dataclass | `genereview_link/corpus/bundle.py` | L14–L62 — schema version fields (`schema_migrations`), provenance fields |
| `validate_database_ready` | `genereview_link/corpus/bundle_validation.py` | L38–L119 — checks chapter/passage/embedding counts + HNSW existence |
| `asset_name_for_release` | `genereview_link/corpus/bundle_metadata.py` | L17–L26 — canonical filename `genereview-corpus-YYYY-MM-DD-rN-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz` |
| Bootstrap restore | `genereview_link/server_lifecycle.py` | L35–L119 — `BUNDLE_URL="latest"` → `resolve_latest` → download + sha256 + `pg_restore` |
| `resolve_latest` | `genereview_link/ingest/github_release.py` | L26–L34 — fetches `/releases/latest` from `GITHUB_REPO` |
| Verify workflow | `.github/workflows/verify-corpus-bundle.yml` | L1–L78 — manual workflow: download → manifest checksum → `pg_restore` → row count smoke test |
| Disabled build stub | `.github/workflows/build-corpus.yml` | L1–L19 — `workflow_dispatch` only, exits immediately with a message |
| `.env.docker.example` | `.env.docker.example` | L27 — `BUNDLE_URL=latest` already set; comment says "convenience default after the first promoted corpus release" |
| Postgres service pattern | `.github/workflows/verify-corpus-bundle.yml` | L19–L30 — `pgvector/pgvector:0.8.2-pg18`, health check, `DATABASE_URL` env |

### What is missing

1. A real `build-corpus-bundle.yml` that runs the full ingest + embed + bundle + release-upload pipeline in Actions.
2. The `BundleManifest` fields `app_git_sha`, `app_version`, `schema_migrations` are declared (bundle.py L41–L43, L38–L40) but the `_build_bundle` helper in `cli.py` (L266–L358) never populates them — a gap in provenance.
3. A `--created-by ci` branch or env-var so the manifest `created_by` field reflects the CI context.
4. Docs for the full publish + Docker bootstrap workflow.

---

## 2. Workflow Design

Replace `.github/workflows/build-corpus.yml` with the following structure.

### Triggers

```yaml
on:
  workflow_dispatch:          # manual kick-off; supports release_id input
    inputs:
      release_id:
        description: "Release ID (YYYY-MM-DD-rN, e.g. 2026-06-12-r1)"
        required: true
        type: string
      dry_run:
        description: "Build bundle but skip GitHub Release upload"
        required: false
        default: "false"
        type: choice
        options: ["true", "false"]
  schedule:
    - cron: "0 3 1 * *"      # monthly, first of month at 03:00 UTC
```

No automatic push trigger — corpus builds are expensive and semi-annual, not
per-commit artifacts.

### Permissions

```yaml
permissions:
  contents: write    # required for gh release create / asset upload
```

### Runner and disk

GitHub-hosted `ubuntu-latest` provides ~14 GB free disk. The corpus dump
(~200–400 MB compressed), HF model cache (~30 MB), and Postgres data
directory (~500 MB) should fit comfortably. If a future corpus exceeds ~6 GB
total, switch to a self-hosted runner with a local NVMe (see Risks).

### Postgres service container

```yaml
services:
  postgres:
    image: pgvector/pgvector:0.8.2-pg18   # matches verify-corpus-bundle.yml L20
    env:
      POSTGRES_PASSWORD: ci
      POSTGRES_DB: genereview
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready
      --health-interval 5s
      --health-timeout 5s
      --health-retries 20
env:
  DATABASE_URL: postgresql://postgres:ci@localhost:5432/genereview
  INGEST_EMBED_DEVICE: cpu     # GitHub runners have no GPU
```

### Steps (one job: `build-bundle`)

| # | Step name | Command / action |
|---|---|---|
| 1 | Checkout | `actions/checkout@v6` (pinned SHA, matching ci.yml L29) |
| 2 | Set up uv | `astral-sh/setup-uv@v7` with `enable-cache: true`, version `0.8.7` (matching ci.yml L35) |
| 3 | Install deps | `uv sync --frozen` |
| 4 | Migrate | `uv run genereview-link migrate` (applies control + data migrations via `apply_control_migrations` / `apply_data_migrations` in `genereview_link/db/migrate.py`) |
| 5 | Ingest | `uv run genereview-link ingest` — populates chapters + passages (cli.py L193–L221) |
| 6 | Embed | `uv run genereview-link embed` — backfills BGE embeddings + builds HNSW index (cli.py L224–L252). Timeout: 60 min. |
| 7 | Smoke query | inline Python via `psql` or `uv run python -c` — call `search_passages(q="BRCA1 risk-reducing mastectomy salpingo-oophorectomy", rerank="rrf")` using the existing `/passages/search` FastAPI route (start server ephemerally) OR run the repository SQL directly. Accept ≥1 result. |
| 8 | Validate | `uv run genereview-link bundle validate` — calls `validate_database_ready` (bundle_validation.py L38) which checks chapter_count ≥ 880, passage_count ≥ 40 000, embedding_count == passage_count, HNSW index present. |
| 9 | Build bundle | `uv run genereview-link bundle build --release-id ${{ inputs.release_id }}` — writes `genereview-corpus-YYYY-MM-DD-rN-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz` + `.sha256` sibling (cli.py L384–L395, bundle.py L92–L118) |
| 10 | Upload Release | `gh release create corpus-${{ inputs.release_id }} <tarball> <tarball>.sha256 --title "corpus-${{ inputs.release_id }}" --notes "..." --repo berntpopp/genereviews-link` (skip if `dry_run=true`) |
| 11 | Trigger verify | `gh workflow run verify-corpus-bundle.yml -f bundle_url=<asset_url>` or use `workflow_call` |
| 12 | Step summary | emit counts (chapters/passages/embeddings) to `$GITHUB_STEP_SUMMARY` |

For the smoke query step the simplest implementation is a direct psql call:

```bash
psql "$DATABASE_URL" -c \
  "select count(*) from genereview.genereview_embeddings_bge384 \
   where model_name='BAAI/bge-small-en-v1.5'" | grep -E '[0-9]+'
```

A vector search smoke test requires starting the server; defer that to the
verify workflow (which already runs `pg_restore` and can be extended with a
curl against a locally started server).

### Concurrency guard

```yaml
concurrency:
  group: build-corpus-bundle
  cancel-in-progress: false   # never abort a running ingest
```

---

## 3. Manifest / Provenance Gaps to Fix

The `BundleManifest` dataclass already declares the right fields but the CLI
helper `_build_bundle` (cli.py L266) does not populate them. Required changes
(small, same file):

- `app_git_sha`: set from `GITHUB_SHA` env var if present, else `git rev-parse HEAD`.
- `app_version`: read from `importlib.metadata.version("genereview-link")` (the package is already 2.0.0 in pyproject.toml).
- `genereview_link_version`: same as `app_version` (redundant alias for downstream tooling).
- `schema_migrations`: query `select namespace, version from public.schema_migrations order by namespace, version` and populate `{"control": [...], "data": [...]}`.
- `created_by`: pass `"ci"` when running inside Actions (detect via `GITHUB_ACTIONS=true`).

These changes live entirely in `_build_bundle` (cli.py L296–L358, ~25 lines
added). The `BundleManifest` dataclass needs no changes — all fields already
exist (bundle.py L38–L55).

---

## 4. Docker Bootstrap

### Current state (already correct)

`.env.docker.example` L27 already has `BUNDLE_URL=latest` with a comment
pointing to pinned-URL usage. The bootstrap path in `server_lifecycle.py`
L68–L119 resolves `"latest"` via `resolve_latest` (github_release.py L26),
downloads with sha256 verification, and calls `pg_restore`.

### Changes needed

1. **`.env.docker.example`**: uncomment `BUNDLE_URL=latest` (it is already
   present but commented — change the `# BUNDLE_URL=latest` line to an active
   default once a release asset exists; the comment block on L18–L27 already
   explains the pattern).

2. **`docker/docker-compose.yml`**: add `BUNDLE_URL` to the `genereview-link`
   service's `environment:` block with a commented default:
   ```yaml
   # BUNDLE_URL: ${BUNDLE_URL:-latest}   # uncomment after first release asset
   ```
   This avoids breaking users who have no release yet.

3. No changes needed to `server_lifecycle.py` — it already handles all three
   modes (bundle download, BUILD_LOCAL, external corpus).

---

## 5. Documentation

Add a new section to `docker/README.md` (or `docs/corpus-bundles.md` if a
dedicated doc is preferred) covering:

- **Building a corpus bundle locally**: `make bundle-publish-local
  RELEASE_ID=YYYY-MM-DD-rN` (Makefile L170–L171) — requires local Postgres
  with populated corpus and `CUDA` or `--device cpu`.
- **Publishing/updating a release**: the `bundle publish-local` command uploads
  via `gh release create` (cli.py L447–L455); alternatively trigger the new
  `build-corpus-bundle.yml` workflow.
- **Docker bootstrap**: explain `BUNDLE_URL=latest` vs pinned URL vs
  `BUILD_LOCAL=true` (server_lifecycle.py L35 docstring).
- **Verifying the deployed corpus**: run `verify-corpus-bundle.yml` manually
  (verify-corpus-bundle.yml L1–L78) or use `bundle validate` against a running
  `DATABASE_URL`.

---

## 6. Risks

### Risk 1 (HIGH): Embedding step feasibility on GitHub-hosted runners

`BAAI/bge-small-en-v1.5` runs on CPU; embedding ~40 k passages at ~100
passages/s = ~400 s (~7 min). GitHub Actions free timeout is 6 h for public
repos, so wall-clock is not a concern. However, `sentence-transformers` model
download requires outbound HF Hub access from the runner. HF Hub has
occasionally rate-limited unauthenticated CI fetches. Mitigation: set
`HF_TOKEN` as a repo secret and pass `--token` to `huggingface_hub.snapshot_download`,
or cache the model via `actions/cache` keyed on model name + hash.

### Risk 2 (HIGH): Runner disk and memory limits

Ubuntu runners have ~14 GB free disk and 7 GB RAM. The Postgres data directory
for ~40 k passages + 384-dim embeddings is estimated at ~400–600 MB. The
compressed dump + tarball adds ~200–400 MB. Total ~1–1.5 GB — well within
limits today. If the corpus grows past ~5 GB (e.g. additional embedding models
or larger passage tables), a self-hosted runner with a local NVMe would be
needed. Monitor using `df -h` in the workflow and fail fast with a clear
message if < 4 GB free before ingest.

### Risk 3 (MEDIUM): NCBI E-utilities availability during CI

The `ingest` step fetches live NCBI data (EFetch + Bookshelf scraping). NCBI
can be slow or transiently unavailable. The `EutilsClient` already enforces
0.11 s rate limiting (AGENTS.md "Respect NCBI rate limits"). Mitigation: set
`NCBI_API_KEY` as a repo secret to get the 10 req/s tier; add `--timeout
60min` to the job step. If NCBI is down the job will fail — acceptable since
this is a monthly cron, not a blocking PR check.

---

## 7. Task Checklist

> All tasks are on a new branch `feat/issue-27-corpus-release-bundles`.

- [ ] **T1** Replace `.github/workflows/build-corpus.yml` with the full
      `build-corpus-bundle.yml` workflow as designed in Section 2. Keep the
      `verify-corpus-bundle.yml` unchanged.

- [ ] **T2** Patch `_build_bundle` in `genereview_link/cli.py` (L266–L358) to
      populate `app_git_sha`, `app_version`, `genereview_link_version`,
      `schema_migrations`, and `created_by` in the `BundleManifest`. Add a
      migration query helper that returns `{"control": [...], "data": [...]}`.

- [ ] **T3** Add repo secrets: `NCBI_API_KEY` (existing key), `HF_TOKEN`
      (optional, for model download rate limiting). Document in
      `docs/corpus-bundles.md`.

- [ ] **T4** Update `.env.docker.example` to activate `BUNDLE_URL=latest` as
      the default (remove the comment marker) with a note that this requires at
      least one published release asset.

- [ ] **T5** Update `docker/docker-compose.yml` to pass `BUNDLE_URL` through
      from the environment (commented-out example in the `environment:` block).

- [ ] **T6** Write docs section in `docker/README.md` covering all four topics
      from Section 5.

- [ ] **T7** Run `make ci-local` to confirm no LOC budget violations, lint, or
      type errors from T2 changes.

- [ ] **T8** Do a manual dry run: trigger `build-corpus-bundle.yml` with
      `dry_run=true` on a fork or staging branch, verify the bundle is built
      and the step summary reports correct counts. Then re-run with
      `dry_run=false` to create the first real release asset.

- [ ] **T9** After the first release asset is uploaded, trigger
      `verify-corpus-bundle.yml` with the asset URL and confirm it passes.

- [ ] **T10** Test Docker bootstrap: `docker compose down -v && docker compose up`
      with `BUNDLE_URL=latest` and verify `search_passages` works (issue #27
      acceptance criterion: `search_passages(q="BRCA1 risk-reducing mastectomy
      salpingo-oophorectomy", rerank="rrf")` returns ≥ 1 result).

---

**Note:** T8–T10 require live GitHub permissions and real NCBI/HF access.
They cannot be validated in a sandbox or auto-merged via an agentic workflow.
The deliverable for this plan is T1–T7 (code + config changes) plus the
workflow YAML draft in T1.
