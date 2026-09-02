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

1. the reviewed release assets are staged in `CORPUS_SEED_DIR` on the host — for the
   current direct (manifest-v3) pin, exactly `corpus.dump`, `manifest.json` and
   `SHA256SUMS`, beside the reviewed `model/` directory the same init materialises — and
2. `CORPUS_DUMP_SHA256`, `CORPUS_MANIFEST_SHA256` and `CORPUS_CHECKSUMS_SHA256` are the
   digests published with that release (the same values as `container-release.json` →
   `.data.digest` and `corpus-release.json` → `.manifest_digest`, `.checksums_digest`), with
   `CORPUS_RELEASE_TAG` naming it. (A legacy single-tarball release uses
   `CORPUS_SEED_PATH=/seed/corpus-bundle.tar.gz` and `CORPUS_BUNDLE_SHA256` instead.)

A third fact follows from them rather than being configured separately: after the restore
(or, on a volume that already holds the corpus, after re-proving the staged artifact against
the rows really present) the sidecar records which reviewed release is serving into
`public.genereview_runtime_data_identity`, and `/health` republishes it as
`release_identity`. That is the `runtime-v1` data identity contract the fleet controller
needs before it may activate a new corpus release — see
[deployment.md § Runtime data identity](deployment.md#runtime-data-identity-runtime-v1).

Both fail closed. An absent artifact and an absent, malformed, or **placeholder** digest
(64 zeroes, 64 `f`s, the empty file's digest) are all refused, because a checksum that
verifies nothing while looking like verification is worse than no checksum.

```bash
# On the server, once per corpus release (direct, manifest-v3 shape):
tag=corpus-data-2026-09-01-r1
base="https://github.com/berntpopp/genereviews-link/releases/download/$tag"
sudo install -d -m 0755 /srv/genefoundry/genereviews-seed
tmp=$(mktemp -d)
for asset in corpus.dump manifest.json SHA256SUMS; do
  curl -fsSL --proto '=https' -o "$tmp/$asset" "$base/$asset"
done
(cd "$tmp" && sha256sum -c SHA256SUMS)                     # verify BEFORE staging
sha256sum "$tmp"/corpus.dump "$tmp"/manifest.json "$tmp"/SHA256SUMS   # must equal the
#   container-release.json .data.digest and corpus-release.json .manifest_digest / .checksums_digest
sudo mv "$tmp"/corpus.dump "$tmp"/manifest.json "$tmp"/SHA256SUMS /srv/genefoundry/genereviews-seed/
# The seed directory must then hold exactly these three files plus the model/ directory:
# move any previous corpus-bundle.tar.gz OUT of it (the restore refuses extra entries).
# Then set CORPUS_SEED_PATH=/seed, CORPUS_RELEASE_TAG=$tag and the three CORPUS_*_SHA256
# values in .env.docker.
```

`docker/ci-prepare-smoke.sh` performs exactly these steps for CI, and is the executable
reference for the shape of the seed directory. It also still handles the legacy
single-tarball shape (`corpus-data-2026-07-13-r1` and earlier: one `corpus-bundle.tar.gz`
at `CORPUS_SEED_PATH`, anchored by `CORPUS_BUNDLE_SHA256`) — see
[Pinning a published corpus into the application](#pinning-a-published-corpus-into-the-application).

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
mutates a database, ingest never fetches, and publication is a separate, deliberate act.

**The corpus is built locally, not in CI.** The embedding pass takes roughly twenty minutes
on 32 cores; a hosted runner cannot do it in a sensible time. So the bytes are produced on
the maintainer's machine and published as an ordinary immutable GitHub release. The
manifest says so in as many words -- `build_provenance: "maintainer-prebuilt"` -- and no
part of this repository claims a CI build provenance or an attestation for corpus data.

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
#     release's published manifest.json to `snapshot` (without --genesis) and it derives
#     and stages the prior-artifact identity for you.
genereview-link ingest \
  --archive ~/genereviews-source/gene_NBK1116.tar.gz \
  --side-data-dir ~/genereviews-source \
  --source-metadata ~/genereviews-source/source-capture.json \
  --prior-manifest ~/genereviews-source/prior-manifest.json

make embed                                        # BGE backfill + HNSW index
make bundle-validate                              # active corpus is publish-ready
RELEASE_ID=<upstream-date>-r1 make bundle-publish-local
```

Migrations are split into **control** (corpus version, refresh log, active embedding) and
**data** (chapters, passages, embeddings, tables, roles, gene symbols) sets.

> [!IMPORTANT]
> `RELEASE_ID`'s date component must equal the upstream `last_updated` date that
> `snapshot` reported (it is printed, and stored as `listing.last_updated` in
> `source-capture.json`). `verify_data_only_bundle` refuses a release ID whose date does
> not match the source snapshot it claims to package.

### Rights: one committed notice, one reviewer

GeneReviews content is copyrighted -- noncommercial research purposes only, retain the
copyright notice and Usage Disclaimer, no further modifications. That determination is
**committed and versioned** in [`data/RIGHTS.json`](../data/RIGHTS.json): the licence name
and URL, the attribution, the citation, the terms URL, the reviewer (the repository owner)
and the date the terms were last reviewed. It is validated by
`genereview_link/corpus/rights_notice.py`, which checks presence and exact shape, requires
the use restriction to say *research use only*, and refuses a review date in the future.

The bundle builder copies the validated notice verbatim into `manifest.json` as
`rights_notice`, so attribution and the use restriction travel with every published byte,
and the verification workflow re-checks that the published block still matches the
committed file. There is no secret, no locator, no second signature and no per-release
sign-off ceremony: to refresh the determination, edit `data/RIGHTS.json` (and
`terms_reviewed_at`) in a reviewed pull request.

Acquisition is not redistribution, so `snapshot` is not gated on any of this; it does
require `--acknowledge-terms` before it writes a byte, and records the committed notice
alongside the fetched files in `snapshot-manifest.json`.

Requests are paced with NCBI's published courtesy interval (`--min-interval`, default
0.34 s, or 0.11 s when `NCBI_API_KEY` is set). The key does not authenticate the bulk FTP
paths this command uses -- it governs the E-utilities plane above -- but the same politeness
floor applies either way.

### The chain, and its genesis

Each release's `source-capture.json` names the release it was built from, and that claim is
proven byte-for-byte against the retained prior `manifest.json`. The first build of a chain
has nothing to point at, so it is marked explicitly: `--genesis` writes `genesis: true` /
`prior_artifact: null` into the capture. A missing prior *without* `--genesis` is still
refused -- the genesis case is declared, never inferred.

### Packaging and publication

`bundle publish-local` packages the already-ingested, already-embedded, validated database
locally; it never uploads, creates a draft, or contacts a release service. It validates,
evaluates and exports the candidate while holding the corpus advisory lock and a
repeatable-read exported snapshot, so the manifest binds the evaluation suite and results
to the exact corpus source tuple, snapshot identifier and `corpus.dump` digest. `make
bundle` does the same without the release-id ergonomics.

The output directory contains exactly three files, and that is exactly what gets published:

| asset | what it is |
| --- | --- |
| `corpus.dump` | PostgreSQL custom-format, data-only |
| `manifest.json` | the full identity of the build, including `build_provenance` and `rights_notice` |
| `SHA256SUMS` | binds `corpus.dump` and `manifest.json` |

Publish them as a release whose tag is `corpus-data-<release id>` and whose target is the
exact revision the bundle records in `app_git_sha`. Treat a published corpus release as
immutable: never move the tag, never replace an asset. Every consumer pins the asset
digests in `container-release.json`, so a mutation is a detectable break rather than a
silent update -- which is exactly why replacing one is never the right move. Cut a new
release id instead.

```bash
cd genereview-corpus-data-2026-09-01-r1
gh release create corpus-data-2026-09-01-r1 \
  --target "$(jq -er .app_git_sha manifest.json)" \
  --title corpus-data-2026-09-01-r1 \
  --notes-file release-notes.md \
  corpus.dump manifest.json SHA256SUMS
```

The release body must state the build provenance plainly -- that the corpus was built on
the maintainer's workstation, the revision it was built at, and the date the terms were
reviewed -- so nobody reading the release page infers a CI build that never happened.

Then verify the published release from scratch, in CI:

```bash
gh workflow run verify-corpus-bundle.yml -f release_tag=corpus-data-2026-09-01-r1
```

`verify-corpus-bundle.yml` downloads the three assets, checks them against `SHA256SUMS`,
runs `verify_data_only_bundle`, requires `build_provenance: maintainer-prebuilt` and a
`rights_notice` equal to the committed `data/RIGHTS.json`, requires the release to be
non-draft and non-prerelease with exactly those three assets and a target commit equal to
the manifest's `app_git_sha`, then restores the dump into a fresh PostgreSQL 18, rebuilds
HNSW from reviewed code and re-derives the counts, the logical content identity, the
computation chain and the evaluation results. It verifies an *already published* release;
nothing about it is a build step.

### Pinning a published corpus into the application

A published corpus does nothing until an application release pins it. In
`container-release.json`, the `data` block names the release and its `corpus.dump`
digest; `corpus-release.json` beside it names the asset and anchors the two control-file
digests. `docker/ci-prepare-smoke.sh` is the only place the stack touches the network and
it proves the bytes against those digests before they reach the restore sidecar:

```json
// container-release.json — the fleet contract's `data` block (no other keys are admitted)
"data": {
  "mode": "restored-database",
  "release_tag": "corpus-data-2026-09-01-r1",
  "digest": "sha256:<corpus.dump>",
  "schema_compatibility": ["0007_embedding_run_identity"],
  "image_allowlist": ["..."]
}
// corpus-release.json — this repository's own pin of WHICH asset carries that digest
{
  "schema_version": 1,
  "release_tag": "corpus-data-2026-09-01-r1",
  "asset_name": "corpus.dump",
  "digest": "sha256:<corpus.dump>",
  "manifest_digest": "sha256:<manifest.json>",
  "checksums_digest": "sha256:<SHA256SUMS>"
}
```

The router's `ReleaseConfig` forbids keys it does not model, so the direct-release
anchors cannot live inside `data` (genereviews-link v5.2.5 was refused by the fleet
contract gate for exactly that); `docker/ci-prepare-smoke.sh` refuses a
`corpus-release.json` whose `release_tag` or `digest` differs from the contract pin.

`data.digest` and the final readiness artifact identity both mean the verified
`corpus.dump` digest. A pin changes only after the immutable release exists; source
preparation never invents an unavailable asset.

### Historical: the pre-manifest-v3 release shape

`corpus-data-2026-07-13-r1` and earlier ship a single `corpus-bundle.tar.gz` + `SHA256SUMS`
with no `manifest.json`; that shape predates the scheme described above, cannot serve as a
prior artifact for it, and is retained only because it was the pinned production corpus.

`scripts/refresh_chapter_metadata_dates.py` refreshes chapter dates against NCBI.
