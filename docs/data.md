# Data & corpus

How `genereviews-link` gets its data, how you load it, and how a maintainer publishes a
new corpus. For the environment variables named here, see
[configuration.md](configuration.md); for the containers that run them, see
[deployment.md](deployment.md).

## Source and provenance

| | |
|---|---|
| Upstream | [GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1116/), NCBI Bookshelf |
| Access paths | NCBI E-utilities (`EUTILS_BASE_URL`) and Bookshelf HTML |
| Data licence | GeneReviews® content © 1993–present University of Washington. SPDX `LicenseRef-GeneReviews` — copyrighted, **not** an open licence. |
| Terms | <https://www.ncbi.nlm.nih.gov/books/NBK138602/> |
| Attribution | Attribute the University of Washington when redistributing. The `get_license` tool and the `genereview://license` resource return the canonical notice. |
| Citation | Every search hit and passage carries `recommended_citation`. Paste it verbatim; never paraphrase or fabricate it. Cite `passage_id` + the chapter NBK id + `chapter_last_updated`. |

## Two data planes

The server has two independent data planes, and only one of them needs a corpus:

- **Live NCBI** — `search_genereviews`, `get_abstract`, `get_links`, `get_fulltext` and
  the live path of `get_genereview_summary` always call NCBI. They work with no corpus and
  stamp their response version as `live:<timestamp>` rather than a corpus version.
- **Corpus-backed passage retrieval** — `search_passages`, `search_passages_batch`,
  `get_passage`, `get_passages_batch`, `get_chapter_metadata`, `get_chapter_section` and
  `get_table` read the ingested Postgres/pgvector corpus. Without one, they return **HTTP
  503** until a corpus is loaded.

### NCBI rate limits

`NCBI_API_KEY` is optional but strongly recommended:

| | Requests/sec | Enforced inter-request delay |
|---|---|---|
| With `NCBI_API_KEY` | 10 | 0.11 s |
| Without | 3 | 0.34 s |

Web scraping uses **3× longer delays** with exponential-backoff retry on 403/429. The
`EutilsClient` enforces these limits; per `AGENTS.md`, do not bypass them. For multi-worker
deployments, set `RATE_LIMIT_STATE_FILE` so workers coordinate through a shared state file.

## The three corpus-loading modes

Selected at startup by environment variable.

### Mode 1 — Restore from a release bundle (`BUNDLE_URL`)

The fastest way to a populated corpus. Set `BUNDLE_URL` to a `.tar.gz` asset URL from the
GitHub Releases page, or to the special value `latest` to auto-resolve the newest release
(only after a bundle has been published):

```bash
BUNDLE_URL=latest DATABASE_URL=postgresql://... genereview-link serve
```

On first boot the server downloads and integrity-verifies the bundle, restores the Postgres
dump, and starts serving. Subsequent boots detect the active corpus and skip the restore.

Set `GITHUB_REPO=owner/repo` (default `berntpopp/genereviews-link`) when hosting your own
releases. Leave `BUNDLE_URL` empty when no release bundle exists: the server still boots,
but corpus-backed passage search returns 503.

> [!WARNING]
> **`EXPECTED_BUNDLE_SHA256` is a security control.** Set it to an independently-trusted,
> **out-of-band** SHA-256 of the bundle, reviewed into your deployment config — *not*
> copied from the download host. The sibling `.sha256` served next to the bundle is a
> transport-integrity check only: a host that can serve a tampered bundle can serve a
> matching checksum too, so it MUST NOT be the sole authenticity gate.
>
> Promotion is **fail-closed**: a downloaded bundle is accepted only when anchored by
> `EXPECTED_BUNDLE_SHA256` or by the in-repo `BUNDLE_DIGEST_ANCHORS` map. With no anchor
> configured, promotion is refused. `ALLOW_UNANCHORED_BUNDLE=true` knowingly accepts
> transport-integrity-only bootstrap (an air-gapped or dev mirror) and nothing else.

`AUTO_PULL_RELEASES=true` starts an hourly release watcher. `BUNDLE_BOOTSTRAP_DIR`
(default `/tmp/genereview-link`) is the writable scratch directory for download and
extraction.

### Mode 2 — Build the corpus locally (`BUILD_LOCAL=true`)

Runs the full ingest pipeline — download from NCBI, parse, embed — on first boot:

```bash
BUILD_LOCAL=true DATABASE_URL=postgresql://... genereview-link serve
```

Expect **15–30 minutes** on first boot; subsequent boots are instant. Requires
`NCBI_API_KEY` for reliable rate limits.

### Mode 3 — External Postgres (no corpus env vars)

Point `DATABASE_URL` at a pre-populated database (managed RDS, Cloud SQL, …). The server
assumes the corpus exists and starts immediately:

```bash
DATABASE_URL=postgresql://user:pass@managed-host/genereview genereview-link serve
```

If the schema is empty, `/passages/search` returns 503 until a corpus is loaded externally.

> In **production Docker** none of these three run in the serving process. The corpus is an
> immutable, digest-pinned data release restored *once* by a no-egress init sidecar, and
> the app image has no restore path at all. See
> [deployment.md § Corpus restore](deployment.md#corpus-restore-production).

## Ingest pipeline (maintainer)

```bash
make db-migrate     # apply control + data migrations against $DATABASE_URL
make ingest         # download → parse → write → swap
make embed          # backfill embeddings + build the HNSW index
make db-reset       # DROP and recreate the genereview schemas (dev only)
```

Migrations are split into **control** (corpus version, refresh log, active embedding) and
**data** (chapters, passages, embeddings, tables, roles, gene symbols) sets.

## Publishing a corpus bundle (maintainer)

The bundle is built locally by the maintainer **on CUDA** — not in CI — and published as a
GitHub Release asset that Docker consumes at startup.

```bash
make cuda-check                                   # verify local PyTorch CUDA availability
make bundle-validate                              # active corpus is bundle-ready
RELEASE_ID=2026-05-12-r1 make bundle-publish-local
```

`make bundle` builds a release bundle from the active corpus without publishing it.

## Corpus freshness

`chapter_last_updated` is carried on every passage and search hit — surface it so a reader
can judge freshness. `scripts/refresh_chapter_metadata_dates.py` refreshes those dates
against NCBI.
