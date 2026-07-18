# C-beta Cross-Encoder Bake-Off Results

**Date:** 2026-05-13
> Historical record

**Bench fixture:** `tests/fixtures/ranking_bench.jsonl` (299 entries)
**C-alpha baseline:** rrf P@1=0.408, MRR@5=0.560, Recall@5=0.826, composite=0.598

## Comparison Table

| Candidate | P@1 | MRR@5 | Recall@5 | Composite | p95 latency ms | Qualified | Disqualification reasons |
|---|---:|---:|---:|---:|---:|---|---|
| rrf baseline | 0.408 | 0.560 | 0.826 | 0.598 | n/a | yes | C-alpha baseline |
| medcpt | 0.585 | 0.633 | 0.696 | 0.638 | 5627.191314997617 | no | exact-symbol-anchor regression count=1; p95 latency 5627.2ms exceeds 750.0ms |
| msmarco_minilm_l12 | 0.579 | 0.631 | 0.699 | 0.636 | 2374.4000059959944 | no | p95 latency 2374.4ms exceeds 750.0ms |
| mxbai_rerank_base | 0.000 | 0.000 | 0.000 | 0.000 | None | no | manual timeout: full benchmark subprocess was stopped after more than 6h40m of continuous CPU without producing a result artifact |
| bge_reranker_base | 0.000 | 0.000 | 0.000 | 0.000 | None | no | not run: previous larger-candidate benchmark did not complete after more than 6h40m; operator should rerun on dedicated staging hardware if BGE evidence is still required |

## Decision

No qualified winner. Reason: no qualified candidate. C-gamma must not ship until C-beta is rerun successfully.

## Hard Gates

- Exact-symbol-anchor top-1 changes disqualify the candidate.
- Pending-improvement re-locks are capped at one per run and require before/after evidence.
- p95 latency over 750ms on the 6-core VPS disqualifies the candidate.

## Adapter Metadata

No winning adapter metadata is selected by this run. C-gamma must not ship a cross-encoder from these artifacts without a successful rerun.
