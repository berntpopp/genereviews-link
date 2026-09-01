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
genereview-link ingest --archive <archive> --side-data-dir <dir> \
  --source-metadata <capture.json> --prior-manifest <prior-manifest.json> \
  --prior-seal-manifest <prior-seal-manifest.json>
                    # verify retained bytes → record identity → parse → write → swap
make embed          # backfill embeddings + build the HNSW index
make db-reset       # DROP and recreate the genereview schemas (dev only)
```

Migrations are split into **control** (corpus version, refresh log, active embedding) and
**data** (chapters, passages, embeddings, tables, roles, gene symbols) sets.

## Packaging a local corpus (maintainer)

Ingest and embedding are explicit prior operations. This command packages the already-ingested,
already-embedded, validated database locally; it never uploads, creates a draft, or contacts a
release service.

```bash
uv sync --group dev --extra cpu --frozen
make bundle-validate                              # active corpus is bundle-ready
RELEASE_ID=2026-05-12-r1 make bundle-publish-local
```

`make bundle` builds a release bundle from the active corpus without publishing it. Rights-gated
publication is deliberately separate: it requires a complete dated affirmative redistribution
record bound to an immutable sealed handoff object. Do not draft, upload, or publish without that
record.

`bundle publish-local` is the ergonomic build-to-seal path: it validates, evaluates, and exports
the candidate while holding the same corpus advisory lock and repeatable-read exported snapshot.
The resulting manifest binds the evaluation suite/results to the exact corpus source tuple,
snapshot identifier, and `corpus.dump` digest, so the directory is accepted by `seal-handoff`.
Metrics copied from another run cannot replace this in-transaction evidence.

The local output is exactly `corpus.dump`, canonical `manifest.json`, and `SHA256SUMS`; it contains
data only, never schema, migrations, application code, environment files, or credentials. Verify
and seal it with `genereview-link bundle seal-handoff --source <directory> --handoff-root <root> \
--publisher-tool <directory-containing-exactly-one-wheel>`.
`publish-handoff` only re-verifies a literal object ID and a complete affirmative, dated rights
record bound to that object, its source SHA-256, corpus-dump SHA-256, and release ID; it deliberately
has no release-service client. The handoff root is owner-only (`0700`), and sealed objects/files are
checked with no-follow file descriptors, exact digest/size/mode manifests, and immutable object IDs.
The sealed publisher wheel name and digest are part of that object identity; the privileged workflow
extracts only that bounded wheel without an index or dependency resolution. Its handoff verifier uses only
the Python standard library. It is launched from a neutral directory under `python -I`, inserts only
the sealed installation target, and proves the verifier module's `__file__` is below that target, so
the source checkout and ambient site packages cannot shadow it in the credentialed job.
A separately privileged automation may act only on that sealed handoff after the rights record exists.

The privileged workflow never consumes a runner artifact or run ID as a handoff. Its protected
sub-48-KiB handoff locator names exactly the sealed `corpus.dump`, `manifest.json`, `SHA256SUMS`,
`seal-manifest.json`, and one publisher wheel by immutable numeric GitHub release-asset URL, size,
and SHA-256, and binds the object ID and build revision. It reconstructs a fresh owner-only handoff,
then the sealed wheel re-verifies its object identity before rights or release logic is reached.

The protected publisher consumes a sub-48-KiB locator for exactly three immutable, numeric GitHub
release assets: `rights-record.json`, `rights-evidence.json`, and `terms-snapshot.html`. Repository,
host, byte-size, and SHA-256 allowlists are enforced before those durable bytes are accepted. The
canonical rights record uses `bundle:` member URIs and binds both snapshots by SHA-256. The public
release retains all three safe records, `seal-manifest.json`, and the sealed publisher wheel as
`publisher-tool.whl`, in addition to the three data-only bundle files. This makes the public decision
and publisher object reconstructable without publishing private keys, tokens, or unrelated reviewer
material. A missing, non-affirmative, malformed, or mismatched record fails before any release or tag
mutation.

Local handoff roots are durable owner-only storage outside both the repository and serving volumes.
They are retained through program closure; a workflow artifact retention deadline is not evidence
that a local handoff object still exists. Record only an object ID together with its exact durable
absolute path after verifying that path—do not infer or claim an object from an earlier log line.

The no-input builder uses a separate sub-48-KiB protected locator to download the exact retained
archive, source-capture metadata, prior release manifest and seal manifest, and three side-data
files from immutable numeric release assets.
Ingest never substitutes a live fetch for this publication path. The corpus manifest binds the
canonical upstream URLs, exact raw listing response digest/size/capture time, archive digest/size and
sorted member/expanded-content identities, exact sorted chapter IDs, and digest/size identity for
each side-data file. These fields and compute-time runtime/model provenance are stored immutably with
the active corpus rows; packaging refuses older database rows that lack the complete identity.
The prior release ID, application revision, manifest digest, and full logical content tuple are
verified from the retained prior manifest before staging is touched. Restore writes the immutable
`public.genereview_release_readiness` marker only after migrations, exactly one data-only restore,
counts, HNSW, source digest, and the reviewed semantic suite succeed; it binds the three logical
volumes `genereview_pg_data`, `genereview_pg_run`, and `genereview_restore_state`.

The privileged workflow downloads only the exact eight public reconstruction assets through byte-
and deadline-bounded allowlisted streams, verifies their API asset IDs/sizes/digests and attestation,
and reads the PostgreSQL archive TOC before restore. Every `pg_dump`, `pg_restore`, and `psql` command
runs from the digest-pinned PostgreSQL 18/pgvector image, matching the server major. Reviewed migrations
run as the owner; archive data restores through the exact restricted `NOINHERIT` role in one transaction.
The verifier recomputes source/content/provenance identities, exact migration file digests, counts,
HNSW presence, representative queries, and the reviewed nonzero evaluation suite from the restored DB.
Release publication inventories drafts as well as published releases and mutates only the exact numeric
release ID after verifying its tag, source revision, asset IDs, sizes, digests, and closed lifecycle state.
Promotion freezes the exact draft representation and ETag before semantic restore, conditionally
rechecks that same ETag, creates/checks the immutable tag only at the final serialized point, and uses
the verified ETag as `If-Match`. Publication automatically dispatches the external verifier with the
exact release ID/tag/target/assets tuple; closure requires its successful acceptance artifact.

## Corpus freshness

`chapter_last_updated` is carried on every passage and search hit — surface it so a reader
can judge freshness. `scripts/refresh_chapter_metadata_dates.py` refreshes those dates
against NCBI.
