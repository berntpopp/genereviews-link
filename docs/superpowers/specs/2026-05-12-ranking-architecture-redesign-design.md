# Ranking Architecture Redesign — Design Spec

**Date:** 2026-05-12
**Author:** senior MCP engineer (Opus 4.7, in collaboration with project maintainer)
**Source review:** `docs/superpowers/reviews/2026-05-12-mcp-llm-deep-toolset-review.md`
**Companion spec:** `docs/superpowers/specs/2026-05-12-deep-review-solutions-design.md` (subsumes B7 from this doc)
**Branch target:** new branch off `main` after `feat/ranking-quality-v1` (Batch B) merges.
**Predecessors:** Batch B (role-aware ranking) — already in flight on `feat/ranking-quality-v1`.

---

## Goal

Fix the **recall-ceiling failure** that role-aware ranking cannot reach. The current pipeline gates dense scoring behind the top-50 lexical candidates ([`genereview_link/retrieval/repository.py:655-670`](../../genereview_link/retrieval/repository.py)). Passages outside that window — confirmed live cases: hemochromatosis target at lexical rank 66, MCAD target at rank 114 — can never be promoted no matter how good the role multiplier or intent boost is.

The fix is structural: run lexical retrieval and dense retrieval **independently corpus-wide**, fuse with RRF, then optionally pass the top-50 of the fused union to a **cross-encoder reranker**. Pick the cross-encoder model empirically against a benchmark built specifically for the GeneReviews corpus, not from off-the-shelf datasets that target a different corpus type.

Target outcome: top-1 is the topically-correct passage on the curated regression set, **including the recall-ceiling cases** (hemochromatosis #66, MCAD #114, BRCA1 risk-reducing surgery, plus reviewer-style prompts surfaced during the cross-LLM deep review).

## Out of scope

- Anything in the companion deep-review-solutions spec (B1, B2–B6, B8–B13 fixes).
- ML-trained intent classifier (keep the rule-based `QUERY_INTENT_BOOSTS` at [`rerank.py`](../../genereview_link/retrieval/rerank.py)).
- Embedding model swap (BGE-small-en-v1.5 stays as the bi-encoder).
- HGVS-aware tokenizer for `tsquery`. Future spec.
- BM25 (`pg_textsearch` extension) replacement for `ts_rank`. Future spec.
- User-curated review indexes (pubtator-style). Future feature.

## Architecture overview

### Current pipeline (broken)

```
query → tsquery lexical retrieval (top 50)
        ↓
        dense scoring (GATED to lexical's 50 candidates only)
        ↓
        RRF fusion
        ↓
        adjusted_score = rrf × role × (1 + intent_boost)   [Batch B already here]
        ↓
        top-N
```

Problem: if the right passage is at lexical rank 51+, dense scoring never sees it, so RRF cannot recover it. Confirmed live: hemochromatosis target at lexical rank 66.

### Redesigned pipeline (Option C from the brainstorm)

```
                      user query string
                              │
            ┌─────────────────┼─────────────────┐
            ▼                                   ▼
   lexical retrieval                    dense retrieval
   (tsquery, top K=200)            (BGE-small embed → pgvector
                                    HNSW, top K=200, corpus-wide)
            │                                   │
            └────────────────┬──────────────────┘
                             ▼
                    UNION (~250-400 unique)
                             ▼
                  RRF fusion across both ranks
                             ▼
       adjusted_score = rrf × role_multiplier × (1 + intent_boost)
                  [existing Batch B logic, unchanged]
                             ▼
              ┌──────────────┴──────────────┐
              │                             │
        rerank=rrf                    rerank=ce
        (stop here)                          │
                                             ▼
                                  cross-encoder rerank
                                  (top 50 of fused union)
                                             ▼
                              re-apply role_multiplier × intent_boost
                              to cross-encoder score
                                             ▼
                                     top-N output
```

`rerank=lexical` short-circuits at the lexical step (returns lexical-only ordering, unchanged from today).
`rerank=off` skips RRF and section_priority tiebreak (debug-only, unchanged from today).

## Mode taxonomy

The `rerank` query parameter on `/passages/search` accepts the existing 3 values plus 1 new value. **`rrf` is upgraded in-place** so existing callers get the fix automatically.

| Mode | Semantics | Latency budget (VPS 6c/24GB, CPU only) |
|---|---|---|
| `lexical` | tsquery only; unchanged from today | ~10-15ms |
| `rrf` (upgraded) | parallel lexical-top-200 ∪ dense-top-200 → RRF → adjusted_score | ~40-60ms |
| `ce` (new) | rrf pipeline → cross-encoder on top 50 → adjusted_score → top-N | ~80-600ms (depends on chosen model — see shortlist below) |
| `off` | debug-only repo order; unchanged from today | ~5ms |

Default mode remains `rrf`. The semantics change for `rrf` callers is an **intended quality improvement** — but it is a real behavior change. Current route code gates dense scoring to lexical candidates at [`api/routes/passages.py:309`](../../genereview_link/api/routes/passages.py) and [`api/routes/passages.py:321`](../../genereview_link/api/routes/passages.py); switching to corpus-wide dense union can perturb exact-symbol or variant-string top-1s that previously happened to rank well only because dense was constrained. The C-α `must_not_regress` gate (see Phase C-α below) is the hard guard against this.

**Documentation update:** `genereview://usage` must clearly state the new `rrf` semantics and add `ce` to the rerank-modes section. The existing tool description on `search_passages` route gains a one-line addition explaining when to pick `ce` (precision-critical queries; accept ~200ms latency).

## Three implementation phases

The work is decomposed into three phases following the standard ML/IR experimental-design pattern: **build the apparatus**, **run the experiment**, **productionize the winner**. The cleanest analogy is a clinical trial — endpoints and protocol are designed before enrollment, enrollment runs the protocol unchanged, and analysis happens only after enrollment closes. The same discipline applies here.

### Why split, not collapse

| Phase | Clinical trial analogue | What it builds |
|---|---|---|
| **C-α** | Protocol design + cohort assembly | Parallel-retrieval pipeline + benchmark dataset + metric (P@1 + MRR@5 + Recall@5) + `make bench-ranking` harness |
| **C-β** | Locked trial execution / assay run | Measure 4 candidate cross-encoders against the benchmark; no production change |
| **C-γ** | Analysis + SOP publication | Implement `rerank=ce` with the chosen model, harden ops, ship |

Collapsing the phases produces specific failure modes:

| If you collapsed… | …you'd fail this way |
|---|---|
| C-α and C-β (build bench while picking model) | Bench keeps changing during eval; candidate results aren't comparable across runs. Same failure mode as "we kept tweaking the endpoint definition during enrollment" — no clean comparison. |
| C-β and C-γ (pick and ship in one step) | Model committed on theory or vibes; no quantified gain to justify the latency cost; no rollback baseline. Same failure mode as "we picked the drug before pre-registering endpoints" — selection bias. |
| Skip C-β entirely (skip benchmark, ship a model) | No way to detect regressions later because the bench was never built. Future model swaps become guesswork. |

**C-α alone delivers half the value** — fixing the recall ceiling (hemochromatosis #66, MCAD #114) requires zero new model dependency, because the bug is the lexical-50 gate, not the absence of cross-encoders. C-α can ship to the public host as soon as it lands.

**C-γ is the precision win on top of C-α.** It only ships if C-β shows meaningful gain over hybrid-only on the benchmark. If no candidate cross-encoder beats `rrf`-upgraded on the composite metric, C-γ is dropped and the spec closes after C-β — that's the discipline the split enforces.

### Phase C-α — Hybrid retrieval + benchmark harness

**Deliverables:**
1. `repository.py` parallel-retrieval path: lexical-top-K and dense-top-K run independently corpus-wide; union is RRF-fused. `rerank=rrf` upgraded to use this path. `rerank=lexical` and `rerank=off` unchanged.
2. Benchmark fixture `tests/fixtures/ranking_bench.jsonl` (~200 queries — see Benchmark design below).
3. `Makefile` target `bench-ranking` that runs the harness across all retrieval modes and emits a comparison table.
4. Scoring script computing P@1, MRR@5, Recall@5 per mode.

**Gate (all three required):**
1. `make ci-local` clean.
2. Benchmark shows hemochromatosis and MCAD targets reachable (in top-10 for upgraded `rerank=rrf`).
3. **Hard regression gate:** all `must_not_regress` entries in `tests/fixtures/ranking_baseline.json` continue to return their locked `expected_top1_passage_id` under upgraded `rerank=rrf`. Currently 3 entries (HFE C282Y allele frequency, tetra-amelia gene WNT3, BRCA1 founder mutation Ashkenazi). Any change in their top-1 fails the phase. This is the guard against the corpus-wide dense union perturbing exact-symbol / variant-string top-1s that previously ranked well partly because dense was constrained to lexical-top-50.

**Effort:** ~1 week.

**No new model dependency.** This phase delivers half the value (recall ceiling fixed) without touching cross-encoders.

### Phase C-β — Model selection benchmark

**Deliverables:**
1. Run the C-α benchmark harness with each candidate cross-encoder (see shortlist below).
2. Generate a comparison table: each model × each metric × each retrieval mode.
3. Empirical model pick committed to spec as an addendum or follow-up doc.
4. Optional: external-validity sanity check using BEIR-bioASQ or NFCorpus via `mteb` or `pyserini` — confirms the picked model isn't overfit to our silver set.

**Gate:** model selection documented; no production change yet.

**Effort:** ~3 days (mostly inference runtime, since the harness is built in C-α).

### Phase C-γ — Cross-encoder mode ship

**Deliverables:**
1. `rerank=ce` mode implemented using the model chosen in C-β.
2. In-process model loading via sentence-transformers or FlagEmbedding (lazy on first use, shared via Linux fork at worker start).
3. LRU cache keyed on `(query_hash, passage_id) → score`, invalidated on `corpus_version` bump.
4. Failure handling: boot-time model load is loud-fail; per-query 30s timeout → graceful fallback to `rrf` mode + `_meta.diagnostics.suggestions += ["ce_timeout_fallback"]`.
5. `genereview://usage` updated with the `ce` mode entry and latency guidance.
6. Smoke tests for the new mode.

**Score combination rule for `rerank=ce` (REQUIRED — decide before C-γ ships).** Cross-encoder outputs are not all calibrated to the same range. Some models return raw logits (can be negative), some return sigmoid probabilities in [0, 1], some return cosine-like similarity. Naively applying `role_multiplier × (1 + intent_boost)` to a raw negative logit will *invert* the role penalty (a cross_reference passage with logit -2 becomes -0.8 after × 0.4, which ranks higher than -2). The architecture diagram's "re-apply role × intent after cross-encoder" step must therefore specify a normalization:

- **Step 1 — normalize CE output to [0, 1]:** apply `sigmoid(logit)` if the chosen model returns logits, or use the model's native bounded output if already in [0, 1] (e.g., `cross-encoder` package's `predict()` with `activation_fct=Sigmoid()`). The C-β bench-off must record the activation choice for each candidate model so C-γ can wire it correctly.
- **Step 2 — combine:** `final_score = sigmoid_ce_score × role_multiplier × (1 + intent_section_boost)`. Same multiplicative pattern as Batch B's `adjusted_score`, but now multiplying a bounded positive value, not a raw logit.

Alternative considered and rejected: keep CE score primary and apply role/intent as small additive prior. Cleaner mathematically (no normalization step) but breaks consistency with the existing Batch B `adjusted_score` semantics, and means cross_reference passages can only be edged out by ties — not actually demoted when CE thinks they're highly relevant. The multiplicative-after-sigmoid pattern preserves the Batch B contract.

The architecture diagram earlier in this spec ("re-apply role_multiplier × intent_boost to cross-encoder score") is shorthand for this normalize-then-multiply rule.

**Gate:** end-to-end smoke against the regression set; cross-encoder mode wins on P@1 vs `rrf` on the benchmark; latency p95 within whatever budget the chosen model implies.

**Effort:** ~1 week.

## Cross-encoder model shortlist for Phase C-β

VPS constraint: **6 cores / 24 GB RAM, CPU-only inference**. Models above ~300M params become impractical at batch=50 on this hardware. The shortlist is pruned accordingly and includes one strong **medical-domain** candidate to test the "biomedical pretraining matters" hypothesis.

| Candidate | Params | License | Why it's on the shortlist | Est. latency (50 pairs, 6-core CPU) |
|---|---|---|---|---|
| **[ncbi/MedCPT-Cross-Encoder](https://huggingface.co/ncbi/MedCPT-Cross-Encoder)** | 110M | Public domain (NIH) | **NCBI-built, biomedical SOTA.** Trained on **255 million query/article pairs from PubMed search logs** ([Jin et al 2023, PMID 37930324](https://pmc.ncbi.nlm.nih.gov/articles/PMC10627406/)). Strongest biomedical publisher / search-log match — but **not guaranteed** to win, because GeneReviews chapters are structured long-form Bookshelf content, not PubMed abstracts. The training distribution shift (search-log query → abstract retrieval) vs deployment (clinical query → chapter passage retrieval) is real and is exactly what C-β measures. | ~150-250ms |
| **[cross-encoder/ms-marco-MiniLM-L-12-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L-12-v2)** | 33M | Apache 2.0 | The "if this wins, ops is trivial" baseline. Multiple recent benchmarks show it beating 10× larger generic models. Tests whether model strength × speed beats domain pretraining. | ~50-80ms |
| **[mixedbread-ai/mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1)** | 184M | Apache 2.0 | Modern (2024) general-domain reranker, BEIR-competitive, fits the 6-core budget comfortably. | ~250-400ms |
| **[BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base)** | 278M | MIT | Anchors "general-SOTA at borderline-acceptable latency". If MedCPT loses to this, biomedical pretraining didn't help on our corpus and we'd revisit assumptions. | ~400-600ms |

**Models considered and dropped** (do not run unless the above 4 all underperform):

| Model | Why dropped |
|---|---|
| BAAI/bge-reranker-v2-m3 (568M) | ~1000ms+ at batch=50 on 6 cores — exceeds practical p95 budget |
| Qwen3-Reranker-0.6B | Same reason; too large for 6-core CPU |
| zerank-1-small (1.7B LoRA) | Won't fit in worker memory once multiplied by worker count |
| MedGemma-4B / -27B | These are **generative** models, not cross-encoders. Used as LLM-as-judge they take seconds per query — orders of magnitude outside our latency budget. Stay with purpose-built rerankers. |

**Optional fifth — only if C-β has spare time.** Add a BioASQ / PubMedBERT-based reranker from [IEETA/BioASQ-14B](https://huggingface.co/IEETA/BioASQ-14B) (Apache 2.0; bundled experimental medical rerankers, includes PubMedBERT and BioBERT variants) as a clearly-labeled **external-validity challenger**. Not in the core 4-model bake-off because it is a bundled experimental collection rather than a clean primary checkpoint, but it would tell us whether *any* biomedical-pretrained model beats MedCPT on our corpus or whether MedCPT is already pulling all the medical-domain value available.

[AronowLab/BOND-reranker](https://huggingface.co/AronowLab/BOND-reranker) was also considered but is entity-normalization focused, not passage retrieval — does not belong in this benchmark.

**Decision criteria.** Highest composite `(P@1 + MRR@5 + Recall@5) / 3` on the benchmark, with two hard gates:

1. **`must_not_regress` gate:** any model that changes the locked top-1 on a `must_not_regress` entry in `tests/fixtures/ranking_baseline.json` is disqualified regardless of composite score. The current 3 must-not-regress queries (HFE C282Y, tetra-amelia WNT3, BRCA1 Ashkenazi founder) anchor known-good behavior.

2. **Latency gate:** any model whose p95 on the bench exceeds 750ms on the 6-core VPS is disqualified. Slow models that win on quality don't ship.

**The hypothesis the benchmark is designed to test**: does NCBI's medical-domain pretraining on PubMed search logs translate into better ranking on GeneReviews chapters than general-purpose rerankers of similar or larger size? Plausible because GeneReviews chapters use the same biomedical vocabulary as PubMed; equally plausible that general-purpose rerankers handle the structured chapter format better. C-β answers this empirically.

If MedCPT wins clearly, the spec recommendation is to ship MedCPT in C-γ. If MiniLM wins, ship MiniLM (5× smaller, ~3× faster). If they're within ~2 percentage points of each other on the composite, ship MiniLM (latency advantage breaks ties).

## Benchmark design

### Sizing and composition

```
~150  silver queries generated by Codex via codex:rescue subagent
       (stratified across section, chapter age, gene popularity, intent)
+   5  existing tests/fixtures/ranking_baseline.json (Bernt's curated regression set)
+ ~25  deep-review failures (hemochromatosis #66, MCAD #114, BRCA1 risk-reducing
       surgery, HFE C282Y, CF F508del, GRIN2B phenotype, Lynch surveillance,
       phenocopy cases, plus reviewer-style prompts from the cross-LLM session)
+ ~30  external-validity entries (MIRAGE / RAGCare-QA queries about genes
       covered by the GeneReviews corpus, gold passage labeled manually)
─────
~210 queries total
```

### Entry schema

```jsonl
{
  "query": "BRCA1 risk-reducing mastectomy salpingo-oophorectomy",
  "expected_top1_passage_id": "NBK1247:0024",
  "expected_top5_passage_ids": ["NBK1247:0024", "NBK1247:0025", "NBK1247:0026"],
  "status": "must_change",  // or "must_not_regress" or "silver"
  "source": "deep_review_session_2026-05-12",
  "intent": "management",
  "section": "management",
  "gene": "BRCA1",
  "chapter_nbk_id": "NBK1247",
  "notes": "Top-1 currently NBK1247:0035 (a cross-reference); should be 0024 (substantive prevention guidance)."
}
```

The `expected_top5_passage_ids` field is new (existing `ranking_baseline.json` only has top-1). For entries without graded labels, top5 is set to `[expected_top1_passage_id]` and Recall@5 collapses to P@1 for those queries.

### Generation method (Codex via codex:rescue)

The benchmark is generated by a `codex:rescue` subagent given:

1. **A chapter worklist** — ~30 chapters stratified across:
   - Sections (management, diagnosis, molecular_genetics, clinical_features, genetic_counseling)
   - Chapter recency (recently updated, 5+ years old)
   - Gene popularity (well-known like BRCA1/CFTR vs rare like GRIN2B)
   - Each chapter contributes ~5 substantive evidence passages

2. **A prompt template** — instructs Codex to read a passage and emit 1-2 clinical queries that a researcher or clinician would use to find this passage. Format: JSONL line with `query`, `expected_top1_passage_id` (the source passage), `intent`, `section`, `notes`.

3. **An output target** — `tests/fixtures/ranking_bench.jsonl`.

The full Codex prompt template is committed to `tests/fixtures/ranking_bench_generation_prompt.md` for reproducibility. The generated dataset itself is the canonical artifact — future regeneration is optional, not required.

### Auto-validation (no second LLM needed)

Once the silver set is generated, validate each entry by running it through the live MCP across all 4 modes (`lexical`, `rrf-upgraded`, `ce`-placeholder-with-MiniLM, `off`):

- **Bucket A** — all 4 modes return the gold in top-5 → confirmed silver, kept as-is.
- **Bucket B** — modes disagree on top-5 → flagged for SME spot-check (Bernt eyeballs ~30-50 entries, corrects gold or rewrites query).
- **Bucket C** — no mode returns the gold in top-50 → either the silver is a perfect test of recall (kept as `must_change`) or the silver is bogus (dropped).

This makes the retrieval pipeline itself the validator — closer to deployment than using a second LLM to grade.

### External-validity check

After the benchmark is built and the cross-encoder picked, run that model on **BEIR-bioASQ** (or **NFCorpus**) via `mteb` or `pyserini`. If our top model also places near SOTA on a public biomedical IR benchmark, we have high confidence. If it wins on our set but is mediocre publicly, treat with suspicion (possible overfitting to silver-set distribution).

## Caching strategy

LRU cache keyed on `(query_hash, passage_id) → cross_encoder_score`. Sized for ~10K entries (≈ 80 bytes per entry × 10K = 800 KB). Invalidated when `corpus_version` changes.

Reasoning:
- For repeat queries, every passage we re-rank is cache-hit. Latency collapses to near-zero.
- For novel queries against the same corpus, dense and lexical retrieval are fast; only the cross-encoder pass on 50 new (query, passage) pairs is the cost.
- For brand-new corpus versions, cache is bust; behavior matches first-call cold start.

Implementation: `functools.lru_cache` is unsuitable because it ignores `corpus_version`. Use a small custom dict-backed LRU with explicit invalidation on the existing corpus-version-change hook (look for where `corpus_version` is computed and add the invalidation there).

## Failure mode and degradation

**Two-stage initialization** — presence-check at boot, actual model load is lazy:

| Stage | What happens |
|---|---|
| **Boot** | Server starts. Checks for the configured cross-encoder model files on disk (just `os.path.exists` on the expected paths, ~milliseconds). If absent and `rerank=ce` is enabled in config, **fail loud** and refuse to start. Other modes still work if `ce` is disabled in config. |
| **First `rerank=ce` request** | Lazy-load the model into the worker process via sentence-transformers / FlagEmbedding. ~2-5 seconds one-time cost. Subsequent requests in the same worker reuse the loaded model. |

| Runtime failure | Behavior |
|---|---|
| Model load fails on first request despite boot-time presence check (e.g., corrupted file) | Server logs error and returns `rrf` results for this request; subsequent requests retry the load. After 3 consecutive failures, disable `ce` for the worker and add `"ce_disabled"` to `_meta.diagnostics.suggestions`. |
| Model loads but inference times out per-query (>30s) | **Graceful degrade.** Return `rrf`-ordered results; add `"ce_timeout_fallback"` to `_meta.diagnostics.suggestions`. Log a warning with query hash + duration. |
| Model loads, inference succeeds, but returns NaN scores | **Skip the cross-encoder step.** Return `rrf` ordering; add `"ce_invalid_score"` to suggestions. |
| First request after `corpus_version` change | Cache bust; first request pays the full cross-encoder cost, subsequent identical requests cache-hit. |

## Model serving

In-process via `sentence-transformers` (works for all four shortlisted models — MedCPT, MiniLM, mxbai, bge-reranker-base). Lazy load on first request to `rerank=ce` (avoids forcing model load for callers who never use the mode).

VPS resource budget (6 cores / 24 GB RAM):
- Assuming ~4 workers (1 thread-pool slot per ~1.5 cores leaves headroom for I/O bound work).
- Worst-case model footprint in this shortlist: bge-reranker-base at ~560 MB FP32 or ~280 MB FP16.
- 4 workers × ~560 MB = ~2.3 GB total (FP32) or ~1.1 GB (FP16). Easily fits.
- Total VPS RAM 24 GB → comfortable headroom for Postgres + corpus cache + the cross-encoder workers. No memory pressure expected at this shortlist's size class.
- Alternative if a future model swap pushes past ~1.5 GB per worker: switch to HuggingFace TEI sidecar (separate container, single model copy, gRPC). Defer until measured pressure.

## Ingest changes

**None required.** This is a pure query-time architecture change. Passage embeddings already exist in `genereview_embeddings_bge384`; HNSW index already exists ("intentionally omitted in the migration; built post-COPY by the embed CLI" per [`db/migrations/data/0003_embeddings_bge384.sql`](../../genereview_link/db/migrations/data/0003_embeddings_bge384.sql)).

Optional future optimization (Batch D, not this spec): pre-compute cross-encoder scores at ingest for the top-N "common intent templates" against each chapter, caching as a materialized view. Defer until production latency pressure justifies it.

## Metrics

Composite `(P@1 + MRR@5 + Recall@5) / 3`, reported alongside each component metric for transparency.

- **P@1**: did the top hit match `expected_top1_passage_id`? Binary per query. Matters because LLM consumers often only read position 1.
- **MRR@5**: reciprocal rank of the first correct hit in top 5. Penalizes near-misses smoothly. Captures "almost got it right" cases.
- **Recall@5**: did any of `expected_top5_passage_ids` appear in the returned top 5? Matters because LLMs read 3-5 hits and synthesize.

Hard gates:
- `must_not_regress` queries: model is disqualified if any `must_not_regress` entry's current top-1 changes to a different passage.
- `must_change` queries: model is disqualified if zero `must_change` entries are fixed (i.e., no demonstrated improvement on known-bad cases).

## Test budget

| Phase | New tests | Notes |
|---|---|---|
| C-α | ~10 unit + integration | parallel-retrieval correctness, RRF fusion math, benchmark harness scoring, `rerank=rrf` smoke against `ranking_bench.jsonl` |
| C-β | 0 new unit tests | measurement only; benchmark report committed to `docs/superpowers/reviews/2026-05-NN-ranking-bench-results.md` |
| C-γ | ~8 unit + integration | model load, lazy init, cache hit/miss, timeout fallback, `rerank=ce` smoke, regression on full `ranking_baseline.json` |

New fixtures:
- `tests/fixtures/ranking_bench.jsonl` (the benchmark dataset)
- `tests/fixtures/ranking_bench_generation_prompt.md` (the Codex prompt template)
- `tests/fixtures/ranking_bench_worklist.json` (the chapter worklist used to seed generation)

## Public-host deployment policy

Based on 2025-2026 MCP rate-limiting best-practice consensus ([MintMCP](https://www.mintmcp.com/blog/rate-limiting-with-mcp), [Cloudflare WAF guidance](https://developers.cloudflare.com/waf/rate-limiting-rules/best-practices/), [Markaicode MCP API protection 2025](https://markaicode.com/mcp-api-protection-2025/)) and the AGENTS.md note that public hosted tools are research-use scoped: the `rerank=ce` mode is **enabled but rate-limited** on the public host, not auth-gated. Three layers of protection — per-IP request rate, global CE concurrency cap, and privacy-safe telemetry — together prevent both runaway agent abuse and disproportionate single-client compute share.

| Setting | Public host | Local Docker |
|---|---|---|
| `rerank=lexical / rrf / off` | Available; modest per-IP rate limit (e.g., **60 req/min**) | Unlimited |
| `rerank=ce` | Available; per-IP rate limit (e.g., **15 req/min**) + **global CE concurrency cap** (e.g., **max 2 in-flight `ce` requests across all workers**). No auth header required. Returns 429 on per-IP overage; returns 503 with `Retry-After` on global concurrency overage. | Unlimited |
| Telemetry | See "Privacy-safe telemetry" below — never raw query, IP-prefix-only, HMAC-hashed query, bounded retention. | Not needed |

**Reasoning for "rate-limited, not gated":**

- This MCP is a **research-use** tool. Requiring auth for the high-quality mode creates friction for legitimate clinicians and researchers — exactly the audience the project exists for.
- A 15 req/min cap is well above any genuine clinical workflow (you're not running `ce` 100× per minute by hand) but caps theoretical abuse at ~21,600 queries/day max per IP. With the global concurrency cap on top, no single client can monopolize CE compute even if it stays under the per-minute limit.
- The cautionary tale informing this design: a runaway AI agent ran a $47K cloud bill in 8 hours via a single MCP ([MintMCP](https://www.mintmcp.com/blog/rate-limiting-with-mcp)). Layered limits make this failure mode structurally impossible: even if the per-IP limiter is somehow bypassed (e.g., distributed botnet), the global concurrency cap keeps total CE compute bounded.
- Auth tokens are an **escalation path, not the starting point**. Move to auth only after observed abuse or if hosting costs become material.

**Important caveat about in-memory limiters and multi-worker deployments.** A naive in-memory per-IP rate limiter (e.g., `slowapi` with the default in-process backend) maintains separate counters per uvicorn/gunicorn worker. With N workers, the effective per-IP limit becomes N × the configured value. For a single-process deployment this is fine; for multi-worker (e.g., gunicorn with 4 workers), either:
- Run a single worker (acceptable for this MCP's traffic profile), OR
- Use a shared-state backend (Redis or a small Postgres advisory-lock-based counter), OR
- Configure the per-IP limit as `(target_limit ÷ worker_count)` to compensate.

The global CE concurrency cap is **always cross-worker** — implement it as a Postgres advisory lock or a single-process asyncio semaphore in a coordinator (e.g., a dedicated `gunicorn` worker handling all CE requests).

**Privacy-safe telemetry.** Never log raw query text. The logging spec for `ce` calls is:

```
log line fields:
  - timestamp
  - ip_prefix         # /24 for IPv4, /48 for IPv6 — coarsening dilutes individual-tracking
  - client_key        # optional: opaque token if the request carried one; null otherwise
  - query_hmac        # HMAC-SHA256(query, rotating server secret); secret rotated weekly
  - retrieval_mode    # 'lexical' | 'rrf' | 'ce' | 'off'
  - latency_ms
  - status_code
  - top_passage_id    # first row of results, useful for abuse pattern detection
```

Retention: 30 days rolling. The rotating HMAC secret means that even within retention, the same query text from different weeks hashes differently — preventing long-term query reconstruction. No PHI by construction (no IP, no raw query, no auth identifier persisted as-is).

**`X-Forwarded-For` is spoofable** unless the public host runs behind a known trusted proxy (CDN, load balancer). Only honor `X-Forwarded-For` if the incoming connection's source IP is in a trusted-proxy allowlist; otherwise use `request.client.host` directly. Misconfiguration here defeats the entire per-IP limit.

**Implementation note**: rate-limiting at the application layer is sufficient for this MCP's scale; no external gateway needed. Use FastAPI middleware (e.g., `slowapi` or a small custom middleware on the search route) with the cross-worker caveats above. Document the configured limits in `genereview://usage` so LLM consumers know what to expect from a 429.

## Open questions (decide before C-γ ships, not blocking C-α)

1. **Model storage location.** Where does the chosen cross-encoder model get downloaded to / mounted from on the VPS? Options: bundle in Docker image (large image but reproducible), pre-warm to a volume mount at container start, on-demand download (network dependency at boot). Recommendation: pre-warm to a volume mount; document the path.

2. **Corpus-version cache invalidation hook.** Where to wire the cache-bust. Likely at the same place `corpus_version` is recomputed (somewhere in `genereview_link/ingest/orchestrator.py` or `genereview_link/services/service_manager.py` — confirm during C-γ implementation).

3. **Rate-limit middleware choice.** `slowapi` (FastAPI-native, redis-optional) vs a small custom in-memory limiter for single-VPS deployment. Recommendation: start with custom in-memory (no new dep); migrate to `slowapi` if running multi-worker with shared state becomes needed.

## Migration order and rollout

| Phase | Wall-clock effort | Risk | Ships |
|---|---|---|---|
| **C-α** Hybrid + harness | ~1 week | low | upgraded `rerank=rrf` + benchmark fixture + `make bench-ranking` |
| **C-β** Model bake-off | ~3 days | none | model picked, written into this spec as an addendum |
| **C-γ** Cross-encoder mode | ~1 week | medium (new model dep) | `rerank=ce` shipped |

Total: ~2.5 weeks serial, or ~1.5 weeks with parallel work (one engineer on C-γ harness while another runs C-β).

## Sources

- [Beyond Retrieval: Ensembling Cross-Encoders and GPT Rerankers for Biomedical QA (arXiv 2507.05577)](https://arxiv.org/html/2507.05577v1)
- [BAAI/bge-reranker-v2-m3 — Hugging Face](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [BAAI/bge-reranker-base — Hugging Face](https://huggingface.co/BAAI/bge-reranker-base)
- [ms-marco-MiniLM-L-12-v2 — Hugging Face](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L-12-v2)
- [mixedbread-ai/mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1)
- [MIRAGE benchmark — Benchmarking RAG for Medicine (ACL 2024)](https://aclanthology.org/2024.findings-acl.372.pdf) — for external-validity entries
- [RAGCare-QA benchmark (medRxiv 2025)](https://www.sciencedirect.com/science/article/pii/S2352340925008674)
- [BEIR datasets — Hugging Face](https://huggingface.co/datasets/BeIR/nfcorpus)
- [Hybrid Search in PostgreSQL — ParadeDB](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [Synthetic data for RAG evaluation — Dylan Castillo](https://dylancastillo.co/posts/synthetic-data-rag.html)
- [FastMCP Tools documentation](https://gofastmcp.com/v2/servers/tools) — for `outputSchema` integration patterns
