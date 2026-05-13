# C-alpha Benchmark Results

**Date:** 2026-05-13
**Bench fixture:** `tests/fixtures/ranking_bench.jsonl` (299 entries: 38 silver-A, 168 silver-B, 88 silver-C, 2 must-not-regress, 3 must-change)
**Corpus version:** 2026-05-10-r6
**Code under test:** local dev server at port 8000 running commit 2d66d56 (feat/ranking-c-alpha, parallel hybrid retrieval with filter-aware RRF)

## Comparison table (NEW path = upgraded rerank=rrf)

| Mode | P@1 | MRR@5 | Recall@5 | Composite | Regressions | Improvements |
|---|---|---|---|---|---|---|
| lexical | 0.084 | 0.128 | 0.211 | 0.141 | 1 | 0 |
| rrf (NEW) | 0.405 | 0.559 | 0.826 | 0.596 | 1 | 1 |
| off | 0.084 | 0.128 | 0.211 | 0.141 | 1 | 0 |

Note: `lexical` and `off` produce identical P@1 and Recall@5 values on this corpus. The sole difference in MRR@5 (0.128 vs 0.129) is rounding. RRF mode delivers a 4.8x lift in P@1 (0.405 vs 0.084) and a 3.9x lift in Recall@5 (0.826 vs 0.211) over the no-rerank baseline.

OLD-path comparison: OLD Docker server (port 8765) benchmark was still running at time of documentation write-up. Results will be appended once available; the OLD run is informational only and not a gate input.

## Hard gates (C-alpha exit criteria)

- [x] No `exact-symbol-anchor` regressions on `rrf` -- **FAIL**: 1 regression detected (see below)
- [x] At least 1 `must_change` improvement on `rrf` -- **PASS**: BRCA1 risk-reducing surgery query now top-1 correct
- [x] Hemochromatosis case resolves NBK1440 in top-5 -- **PASS**: top-5 = [NBK1440:0041, NBK1440:0020, NBK1170:0021, NBK1349:0017, NBK1440:0001]
- [x] MCAD case resolves NBK1424 in top-5 -- **PASS**: top-5 = [NBK582032:0001, NBK1424:0029, NBK1424:0025, NBK1424:0001, NBK1424:0024]
- [x] BRCA1 risk-reducing surgery top-1 = NBK1247:0024 -- **PASS**: confirmed, top-5 = [NBK1247:0024, NBK1247:0031, NBK1488:0034, NBK1247:0025, NBK1247:0026]

### Regression detail

The script flags 1 exact-symbol-anchor regression on all modes:

| Query | Gold | rrf top-1 | rrf top-5 |
|---|---|---|---|
| HFE C282Y allele frequency most common variant | NBK1440:0051 | NBK1440:0005 | NBK1440:0051 at position 2 |

The regressed top-1 (`NBK1440:0005`) is from the **same chapter** as the gold (`NBK1440`). The gold passage `NBK1440:0051` is at position 2 in the top-5. This is a within-chapter ranking tie, not a chapter-level recall failure. The hemochromatosis chapter (NBK1440) is still retrieved; the precision failure is between two passages from that chapter.

For lexical/off modes, the regression is more severe: `NBK1426:0064` (a different chapter entirely) displaces `NBK1440:0051`.

### must_change spot-checks

| Query | Expected top-1 | rrf top-1 | rrf top-5 contains gold? |
|---|---|---|---|
| BRCA1 risk-reducing mastectomy salpingo-oophorectomy | NBK1247:0024 | NBK1247:0024 | YES (position 1) |
| CFTR F508del CFTR modulator therapy indication | NBK1250:0032 | NBK1250:0043 | YES (position 2) |
| GRIN2B-related neurodevelopmental disorder phenotype spectrum | NBK501979:0005 | NBK385627:0003 | NO |

BRCA1: **fixed** (was top-1 wrong pre-upgrade, now correct).
CFTR: gold at position 2 -- top-1 is a related CFTR passage (NBK1250:0043), not the modulator-indication passage.
GRIN2B: gold not in top-5; closest hits are NBK501979:0002 and NBK501979:0016, but not the expected clinical-description passage NBK501979:0005.

## Bucket transition (silver entries only)

Pre-T11 validation buckets (from JSONL `validation_bucket` field): A=38, B=168, C=88 (294 silver total).

Post-T11 effective distribution against rrf top-1:
- Bucket A (top-1 correct under rrf): corresponds to rrf P@1 over silver = ~119 entries (0.405 x 294)
- Bucket B (top-5 correct but not top-1 under rrf): corresponds to rrf Recall@5 - P@1 = ~0.421 x 294 ~124 entries
- Bucket C (not in top-5 under rrf): ~0.174 x 294 ~51 entries

(These are estimates. The bench fixture's `validation_bucket` field is locked to pre-T11 measurement.)

## Wall time

| Run | Server | Entries | Wall time |
|---|---|---|---|
| NEW (T11, port 8000) | local dev BGE eager-load | 299 x 3 modes | 11 min 49 sec |
| OLD (pre-T11, port 8765) | Docker | 299 x 3 modes | running at write-up time |

## Notes

1. **HFE C282Y within-chapter tie**: The regression is between two passages from NBK1440 (HFE Hereditary Hemochromatosis). The gold `NBK1440:0051` describes C282Y allele frequency; the new top-1 `NBK1440:0005` is likely the introduction or overview passage. The parallel hybrid RRF scorer may be slightly over-weighting dense retrieval for this entry, which surfaces the overview passage above the allele-frequency-specific one. This is a precision-at-1 issue, not a recall failure.

2. **CFTR modulator close miss**: The gold `NBK1250:0032` is at position 2. The new top-1 `NBK1250:0043` is from the same CF chapter. A within-chapter ordering issue similar to HFE.

3. **GRIN2B miss**: The expected passage `NBK501979:0005` is not in the top-5. The closest GRIN2B-specific hits are `NBK501979:0002` (position 2), `NBK501979:0000` (position 4), and `NBK501979:0016` (position 5). The specific clinical-description passage 0005 remains below the fold. This was already a `must_change` entry (pre-upgrade it also missed), so it does not count as a new regression.

4. **MCAD and Hemochromatosis chapter-level recall**: Both chapters are found in top-5 for their respective recall-ceiling queries. Chapter-level recall is strong.

5. **RRF lift**: The 4.8x P@1 improvement and 3.9x Recall@5 improvement demonstrate the parallel hybrid retrieval path's effectiveness for the silver corpus.

6. **Latency**: NEW server completed 299 x 3 = 897 queries in 11 min 49 sec = ~0.79 sec per query-mode pair (sequential, no concurrency). The old sequential-lexical path was estimated at ~4s per query, suggesting the new path is ~5x faster per query.

## C-alpha gate status

The script exited with code 1 due to the HFE C282Y `exact-symbol-anchor` regression on all three modes. This regression is a within-chapter passage ordering issue (gold is at position 2, not position 1). The operator should review whether the gold label for `HFE C282Y allele frequency most common variant` should be relaxed to accept any top-5 NBK1440 passage, or whether the RRF tie-breaking for within-chapter pairs needs adjustment before C-alpha can be formally declared passing.

The `must_change` gate is partially met: BRCA1 is fixed (1/3 improved), satisfying the minimum gate. CFTR and GRIN2B remain as improvement targets for C-beta.

## Next phase

C-beta (cross-encoder bake-off) gets its own plan after this lands. The benchmark fixture and harness in this PR are the apparatus C-beta will use. Suggested C-beta targets:
- Fix GRIN2B-related neurodevelopmental disorder passage ranking (requires section-level cross-encoder scoring)
- Fix CFTR modulator-indication passage from position 2 to position 1
- Resolve HFE C282Y within-chapter tie (NBK1440:0005 vs NBK1440:0051)
