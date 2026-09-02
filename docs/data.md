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
| Data licence | GeneReviews® content ©1993-2026 University of Washington, Seattle. SPDX `LicenseRef-GeneReviews` — copyrighted, **not** an open licence. |
| Terms | Official snapshot from <https://www.genereviews.org/>: noncommercial research purposes only; retain the copyright notice and Usage Disclaimer; no further modifications. |
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

## How a corpus gets loaded

### Production: an immutable data release, restored once by a sidecar

Production uses exactly one mechanism. The corpus is a digest-pinned GitHub **data
release**, and it is restored into the Postgres volume once, by the no-egress
`genereview-corpus-restore` init sidecar. **The serving application has no restore path at
all** and never downloads anything — that is the point of the design, not an accident of
it. See [deployment.md § Corpus restore](deployment.md#corpus-restore-production).

The operator's side is two facts, both of which must be true before the first start:

1. the reviewed release asset is staged in `CORPUS_SEED_DIR` on the host, and
2. `CORPUS_BUNDLE_SHA256` is the digest published with that release (the same value as
   `container-release.json` → `.data.digest`).

Both fail closed. An absent artifact and an absent, malformed, or **placeholder** digest
(64 zeroes, 64 `f`s, the empty file's digest) are all refused, because a checksum that
verifies nothing while looking like verification is worse than no checksum.

```bash
# On the server, once per corpus release:
tag=corpus-data-2026-07-13-r1
digest=4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc
sudo install -d -m 0755 /srv/genefoundry/genereviews-seed
curl -fsSL --proto '=https' -o /tmp/corpus-bundle.tar.gz \
  "https://github.com/berntpopp/genereviews-link/releases/download/$tag/corpus-bundle.tar.gz"
echo "$digest  /tmp/corpus-bundle.tar.gz" | sha256sum -c -   # verify BEFORE staging
sudo mv /tmp/corpus-bundle.tar.gz /srv/genefoundry/genereviews-seed/
# then set CORPUS_BUNDLE_SHA256=$digest in .env.docker
```

`docker/ci-prepare-smoke.sh` performs exactly these steps for CI, and is the executable
reference for the shape of the seed directory.

### Rebuilding the corpus and publishing it as a release asset

A corpus is built on a workstation and shipped as an artifact; servers only ever restore
one. There is exactly one flow, end to end — see
[Building and publishing a corpus](#building-and-publishing-a-corpus-maintainer) below.
Once the release exists, update `container-release.json` (`.data.release_tag` and
`.data.digest`), then stage and pin it on the server as above.

### Development: `BUILD_LOCAL=true` is inert

`BUILD_LOCAL` named a boot-time live ingest that no longer exists. Ingest consumes only a
retained offline source set, so the branch behind the flag could only ever raise; it now
logs an explicit error and the server degrades exactly as it does with an empty database
(`/passages/search` → **503**). To get a corpus on a workstation, run the maintainer flow
below (`snapshot` → `ingest --genesis` → `embed`) against your own `DATABASE_URL`.

### External Postgres (no corpus env vars)

Point `DATABASE_URL` at a pre-populated database. The server assumes the corpus exists and
starts immediately; if the schema is empty, `/passages/search` returns **503** until a
corpus is loaded externally.

> [!NOTE]
> `BUNDLE_URL` is **inert**. Until 2026-07-13 the serving process downloaded and restored a
> release bundle itself; that path was removed when the no-egress sidecar landed, because
> restoring inside the request-serving process gave it exactly the egress and database
> rights the restored-database contract exists to deny it. A deployment that still sets
> `BUNDLE_URL=latest` is not downloading anything. `EXPECTED_BUNDLE_SHA256`,
> `ALLOW_UNANCHORED_BUNDLE`, and `BUNDLE_BOOTSTRAP_DIR` belong to that same removed path.

## Embedding provider

Dense retrieval only works when the query vector and the stored passage vectors come from
the **same** model. The corpus is embedded with `BAAI/bge-small-en-v1.5` (revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, 384-d), recorded in
`public.genereview_active_embedding`.

### How the model reaches the container

The weights are 127 MiB, and the fleet OCI content policy caps any single file in an image
at 64 MiB. PyTorch is further out of reach: its wheel alone is 526 MB. So the serving image
carries **ONNX Runtime** (largest file 30 MB) and the weights arrive the way every other
large artifact in this fleet arrives — as a digest-pinned artifact staged on the host and
materialised once into a named volume by the no-egress init sidecar:

```
  workstation / CI                host                        containers
  ────────────────                ────                        ──────────
  genereview-link model stage ──▶ /srv/genefoundry/           ┌ genereview-corpus-restore
    (verifies every byte against    genereviews-seed/model/   │   /seed        (bind, ro)
     the digests pinned in            model.onnx              │   → verifies, copies
     model_identity.py)               tokenizer.json          │   /data
                                                              └   (volume, rw)
                                                              ┌ genereview-link
                                                              │   /data
                                                              └   (volume, **ro**) → verifies again
```

Nothing is downloaded at runtime. The artifact is proven twice — once by the sidecar before
it is written, once by the server before the ONNX session opens — so a substituted model
never reaches the parser at either end. `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`
make any stray Hub call fail rather than silently fetch.

`model.onnx` is the **same weights** as `model.safetensors`, and parity with the
sentence-transformers path that built the corpus is measured, not assumed: minimum cosine
`0.999999999796`, maximum per-dimension delta `1.75e-07`
(`tests/unit/test_onnx_embedding_parity.py`). CLS pooling, L2 normalisation, the 512-token
limit and the query prefix are pinned in reviewed code — the artifact supplies tensors, and
can never change how its own output is computed.

### Staging it

```bash
# once per model pin, on a workstation or in CI
genereview-link model stage --output /srv/genefoundry/genereviews-seed/model
```

It refuses to write anything whose digest does not match
`genereview_link/retrieval/model_identity.py`, so the download host is not trusted. CI does
exactly this in `docker/ci-prepare-smoke.sh`.

### Provider selection

| `GENEREVIEW_EMBEDDING_PROVIDER` | What runs | Where |
|---|---|---|
| `onnx` *(production default)* | the pinned BGE weights under ONNX Runtime | serving image |
| `torch` | the same weights under sentence-transformers | offline ingest only (`--extra cpu`) |
| `fake` | a deterministic stub; vectors **not** comparable with the corpus | tests / local dev |

The stub is not a lightweight approximation. Fusing its nearest neighbours into
reciprocal-rank fusion promotes arbitrary passages over genuinely matching lexical hits, so
the server:

- refuses to start in production on the stub unless `GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true`;
- disables the dense path and reports `rerank_used: "lexical"` whenever the live provider
  is not the corpus's model;
- reports the provider actually loaded in `_meta.dense_model_id` — never the reference
  model it did not load;
- refuses to serve when a real provider disagrees with the corpus's recorded model;
- reports `status: "degraded"` from `/health` while any non-reference provider is active.

Measured on the built image, under `--read-only --cap-drop ALL --network none`: verify +
load **310 ms**, first query **12 ms**, warm query **6.9 ms**.

## Corpus freshness

`chapter_last_updated` is carried on every passage and search hit. An hourly watcher
(`RELEASE_WATCHER_ENABLED=true`) compares the newest published corpus release with the
pinned one and records the result in `public.genereview_refresh_log`:

```sql
select check_time, decision, detail->>'latest_release_tag'
from public.genereview_refresh_log order by check_time desc limit 5;
```

`decision` is one of `current`, `stale`, `no-active-corpus`, `upstream-unavailable`. It
never pulls: a `stale` row is a prompt to update `container-release.json`, stage the new
asset, and redeploy.

`AUTO_PULL_RELEASES` is **refused at startup**. It named an automatic pull that was never
implemented — the branch behind it was a bare `pass` — so for months it read as "corpus
updates are handled" while doing nothing, which is why `genereview_refresh_log` had zero
rows and the corpus sat at 2026-05-12 unnoticed (#145).

The watcher above only fires hourly, only when explicitly enabled, and only into a log
table — none of which pages anyone. `GET /health` reports the same fact more directly, on
every probe: `corpus.data_as_of` is `genereview_corpus_version.ingest_finished_at` for the
active corpus, restored verbatim from the release bundle, so it is the exact moment that
bundle's content was finalised upstream. `corpus.stale` (and, once true, an overall
`status: "degraded"`) flips once `corpus.age_days` exceeds `CORPUS_MAX_AGE_DAYS` (default
`90`) — no watcher, comparison, or upstream reachability required:

```jsonc
// GET /health
{
  "status": "degraded",
  "corpus": {
    "version": "2026-05-12-r1",
    "data_as_of": "2026-05-12T09:14:03+00:00",
    "age_days": 113,
    "max_age_days": 90,
    "stale": true
  }
}
```

An app assembled outside the normal server lifespan (unit tests, embedded use) reports
`corpus: null`-shaped facts (`version`/`data_as_of`/`age_days` all `None`) with
`stale: false` — absence of state is not evidence of staleness, mirroring how
`embedding_health` treats a never-initialised provider.

## Building and publishing a corpus (maintainer)

One flow, start to finish, on a workstation. Every step is explicit: acquisition never
mutates a database, ingest never fetches, and publication is rights-gated and separate.

```bash
uv sync --group dev --extra cpu --frozen
make db-migrate                                   # control + data migrations into $DATABASE_URL

# 1. Acquire. Fetches the NCBI litarch listing, the GeneReviews archive and the three
#    side-data files into exactly the layout ingest consumes, and records what it fetched
#    in snapshot-manifest.json. Re-running resumes: only the tiny listing is refetched
#    unless upstream moved, a digest no longer holds, or you pass --refresh.
genereview-link snapshot --dest ~/genereviews-source --acknowledge-terms --genesis

# 2a. Ingest, first build of a chain (no prior release exists under this scheme).
genereview-link ingest --genesis \
  --archive ~/genereviews-source/gene_NBK1116.tar.gz \
  --side-data-dir ~/genereviews-source \
  --source-metadata ~/genereviews-source/source-capture.json

# 2b. Ingest, every subsequent build: chained to the previous release. Pass the previous
#     release's manifest.json / seal-manifest.json to `snapshot` (without --genesis) and
#     it derives and stages the prior-artifact identity for you.
genereview-link ingest \
  --archive ~/genereviews-source/gene_NBK1116.tar.gz \
  --side-data-dir ~/genereviews-source \
  --source-metadata ~/genereviews-source/source-capture.json \
  --prior-manifest ~/genereviews-source/prior-manifest.json \
  --prior-seal-manifest ~/genereviews-source/prior-seal-manifest.json

make embed                                        # BGE backfill + HNSW index
make bundle-validate                              # active corpus is publish-ready
RELEASE_ID=<upstream-date>-r1 make bundle-publish-local
make db-reset                                     # DROP and recreate the schemas (dev only)
```

Migrations are split into **control** (corpus version, refresh log, active embedding) and
**data** (chapters, passages, embeddings, tables, roles, gene symbols) sets.

> [!IMPORTANT]
> `RELEASE_ID`'s date component must equal the upstream `last_updated` date that
> `snapshot` reported (it is printed, and stored as `listing.last_updated` in
> `source-capture.json`). `verify_data_only_bundle` refuses a release ID whose date does
> not match the source snapshot it claims to package.

### Acquisition (`snapshot`) and the terms

GeneReviews content is copyrighted — noncommercial research purposes only, retain the
copyright notice and Usage Disclaimer, no further modifications. Acquisition is not
redistribution, so `snapshot` is not behind the publication rights gate; it does require
`--acknowledge-terms` before it writes a byte, and records that acknowledgement alongside
the fetched files in `snapshot-manifest.json`. **Publication remains gated** by the dated
owner determination `rights.py` checks (issue #27) — that is unchanged.

Requests are paced with NCBI's published courtesy interval (`--min-interval`, default
0.34 s, or 0.11 s when `NCBI_API_KEY` is set). The key does not authenticate the bulk FTP
paths this command uses — it governs the E-utilities plane above — but the same politeness
floor applies either way.

### The chain, and its genesis

Each release's `source-capture.json` names the release it was built from, and that claim is
proven byte-for-byte against the retained prior `manifest.json` / `seal-manifest.json` pair.
The first build of a chain has nothing to point at, so it is marked explicitly:
`--genesis` writes `genesis: true` / `prior_artifact: null` into the capture, and the
resulting `seal-manifest.json` carries `genesis: true` / `prior: null`. A missing prior
*without* `--genesis` is still refused — the genesis case is declared, never inferred.

### Packaging, sealing and publication

`bundle publish-local` packages the already-ingested, already-embedded, validated database
locally; it never uploads, creates a draft, or contacts a release service. `make bundle`
does the same without the release-id ergonomics.

Rights-gated publication is deliberately separate: it requires a complete dated affirmative
redistribution record bound to an immutable sealed handoff object. Do not draft, upload, or
publish without that record.

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

The build job uploads one immutable, 90-day Actions artifact named from the sealed object ID. It
contains the exact five attested subjects plus canonical `handoff-materialization.json`, which
binds their names, sizes, SHA-256 digests, build revision, source repository/ref, and object ID.
This unprivileged artifact is only a bridge for the owner: download those exact content bytes, verify
their build-provenance attestations and materialization record, restore the generic
`publisher-tool.whl` filename to the sealed wheel name recorded in that materialization and
`seal-manifest.json`, then copy the five subjects to durable immutable release-asset storage and
construct the numeric-asset handoff locator. Record the
Actions artifact ID/digest and the resulting durable asset IDs/digests in the owner evidence. The
privileged publisher never consumes a runner artifact or run ID as a handoff. Its protected
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

Local handoff roots and the final numeric release assets are durable owner-controlled storage outside
both the repository and serving volumes. They are retained through program closure. The intermediate
90-day workflow artifact makes the attested bytes retrievable after the build job, but its retention
deadline is not final durable-publication evidence. Record only verified exact local or release-asset
identities—do not infer or claim an object from an earlier log line.

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
Before any draft or asset mutation, the owner-created annotated corpus tag must resolve to the exact
build/object identity under an active tag ruleset that prohibits deletion and updates with no
bypass actors. Promotion freezes that tag object plus the exact draft representation and ETag before
semantic restore, rechecks the protected tag/ruleset and ETag immediately before the only PATCH, and
uses the verified ETag as `If-Match`. Publication automatically dispatches the external verifier with the
exact release ID/tag/target/assets tuple; closure requires its successful acceptance artifact.

### Handing a sealed bundle to CI

Two workflows finish the job, and neither is a local step:

| Workflow | Who dispatches it | What it does |
|---|---|---|
| `corpus-data-release.yml` (`-f object_id=<sealed sha256>`) | the maintainer, from `main` | the rights-gated **publisher**: reconstructs the sealed handoff from durable release assets, re-verifies rights, then creates the tag/release. Gated by the `data-release` environment (owner approval). |
| `verify-corpus-bundle.yml` | `corpus-data-release.yml`, automatically | the independent **verifier** of an already drafted/published release: it downloads the eight public assets, restores the dump, and recomputes every identity. |

`verify-corpus-bundle.yml` is **not** a pre-publication check of a local directory and is not
normally dispatched by hand. Its eight inputs all describe a release that already exists:
`release_tag`, `release_id` (the numeric GitHub release ID), `target_commit`, `assets_sha256`,
`publication_nonce`, `verification_phase` (`prepublication` | `postpublication`), `release_etag`,
and `dispatch_time`. It refuses to run off `refs/heads/main`, and its first act is a
`If-None-Match` precondition that must return `304` — so a hand-typed dispatch with a stale
ETag fails closed by design.

Dispatching `corpus-data-release.yml` with an **empty** `object_id` selects the in-CI build
job instead of the publisher. That path assembles its retained source set from a protected
locator rather than from `snapshot`.

> [!NOTE]
> Both CI paths need owner-provisioned protected configuration that is not in this
> repository: `GENEREVIEWS_SOURCE_LOCATOR` + `GENEREVIEWS_SOURCE_REPOSITORIES` (in-CI build
> and verifier), `GENEREVIEWS_HANDOFF_LOCATOR` + `GENEREVIEWS_HANDOFF_REPOSITORIES`,
> `GENEREVIEWS_RIGHTS_LOCATOR` + `GENEREVIEWS_RIGHTS_REPOSITORIES`, and
> `GENEREVIEWS_TAG_RULESET_ID` (publisher). Without them the workflows fail closed, which
> is the intended behaviour — but it also means a corpus can be *built* locally long
> before it can be *published*.

### Historical: the pre-manifest-v3 release shape

`corpus-data-2026-07-13-r1` and earlier ship a single `corpus-bundle.tar.gz` + `SHA256SUMS`
with no `manifest.json` and no seal manifest; that shape predates the sealed-handoff scheme
described above, cannot serve as a prior artifact for it, and is retained only because it is
the currently pinned production corpus.

The repository owner supplied an affirmative redistribution determination dated 2026-09-01;
publication remains bound to that durable evidence, the exact source/artifact digests, and required
University of Washington attribution. The current production pin remains the truthful legacy tar
release until the authorized replacement is actually published and verified. The restore bridge
also accepts the exact-eight release directly: its read-only seed directory contains exactly
`corpus.dump`, `manifest.json`, and `SHA256SUMS`, and `container-release.json` sets `asset_name` to
`corpus.dump` while anchoring all three asset digests. In that direct shape, `data.digest` and the
final readiness artifact identity both mean the verified `corpus.dump` digest. A pin changes only
after the immutable release exists; source preparation never invents an unavailable asset.

`scripts/refresh_chapter_metadata_dates.py` refreshes chapter dates against NCBI.
