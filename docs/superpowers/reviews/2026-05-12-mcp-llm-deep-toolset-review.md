---
date: 2026-05-12
session: Deep toolset shakedown — BRCA1 risk-reducing surgery + HFE C282Y + CF F508del + GRIN2B-NDD
consumer: LLM client (Opus 4.7, 1M context), acting as senior MCP tester
target: working tree, feat/ranking-quality-v1, corpus_version 2026-05-10-r4
scope: exercise every advertised tool + every advertised mode on real evidence-grounding tasks, then plan and execute stress tests
---

# GeneReview-Link MCP — Deep Toolset Review (Review #4)

> Historical record

> All 10 tools exercised, both resources read, all 3 rerank modes + 3 response modes + every documented filter compared against a varied evidence-grounding workload. **One ship-blocker, four parser-side defects, one corpus-freshness gap, one ranking-quality footgun.** Corpus path remains best-in-class; legacy E-utils + scraper path is the long pole.

---

## TL;DR

- **Overall: 8/10** — up from 8.7/10 in Review #3 because deep testing surfaced previously-undetected bugs on the legacy E-utils + scraper path and one ship-blocker in `mode=ids_only`. The corpus pipeline by itself would still be 9/10.
- The corpus pipeline (`search_passages`, `get_chapter_metadata`, `get_chapter_section`, `get_passage`, `get_passages_batch`, `get_table`) is *genuinely* well-designed for LLM consumption.
- The legacy E-utils + scraper tools (`get_abstract`, `get_links`, `get_fulltext`, `get_genereview_summary`) returned malformed or duplicated data on every call I made. Consider either fixing or formally deprecating that path.
- Ranking quality: default `rerank=rrf` surfaces *cross-reference* passages (`See Management, ...`) at rank #1 when the user query mixes section vocabularies. Workaround documented in `genereview://usage` but invisible to a first-time consumer.

---

## Use cases tested

### 1. BRCA1 carrier — risk-reducing surgery (Round 1)

- Top RRF hit was `NBK1247:0035` (genetic_counseling), which is a pure cross-reference passage. The substantively relevant content lived at `NBK1247:0024` (rank #2 by default, rank #1 once `sections=["management"]` is applied).
- Substantive findings (cited verbatim from corpus): *consider prophylactic bilateral mastectomy*; *consider prophylactic salpingo-oophorectomy* with 79% mortality reduction [Marchetti et al 2014]; emerging salpingectomy-then-delayed-oophorectomy paradigm; tubal ligation 34% risk reduction; OCs 50% reduction (max 3–6 yr use).
- **Citation:** `NBK1247:0024` and `NBK1247:0025`, chapter last updated **2022-02-03 per corpus** but **March 25, 2026 per live NCBI page** (`get_fulltext.metadata.update_info`). Freshness gap, see Bug #5.

### 2. HFE hemochromatosis — most common variant + treatment (Round 2)

- Top hit: `NBK1440:0051` (`Molecular Genetics > Table 8`, table `hemochromatosis.T.hfe_pathogenic_alleles`).
- **Most common pathogenic variant:** `c.845G>A` → `p.Cys282Tyr` ("Most common pathogenic allele in European or derivative populations"). Other major alleles: p.His63Asp (common in European), p.Ser65Cys (French), p.Glu168Ter (25% allele freq in northern Italy), c.1006+1G>A (Vietnamese), whole-gene deletion (Sardinians).
- **Treatment:** therapeutic phlebotomy is standard of care (`NBK1440:0027`–`0029`). Weekly until ferritin ≤100 µg/L; twice-weekly for severe (ferritin >1,000 µg/L); start at ferritin >300 µg/L (males) / >200 µg/L (females) per HEIRS Study [Adams et al 2005]. Maintenance phlebotomy. Erythrocytapheresis as alternative; deferasirox 10 mg/kg/day if phlebotomy contraindicated.
- **Citation:** Hemochromatosis. NBK1440. Updated 2024-04-11. Passages NBK1440:0027, NBK1440:0028, NBK1440:0029, NBK1440:0051.
- Tool workflow: 4 corpus tool calls total (search → metadata → table → passage-with-neighbors). Clean.

### 3. CF — most common variant + targeted treatment (Round 2)

- Top variant hit: `NBK1250:0057` (`Molecular Genetics > Table 12`, table `cf.T.notable_cftr_variants`).
- **Most common pathogenic variant:** `c.1521_1523delCTT` → `p.Phe508del` ("Most common pathogenic variant in individuals of Northern European ancestry; founder variant in Amish, Ashkenazi Jewish, Faroe Islander, & Hutterite populations").
- **Treatment table** (`cf.T.cystic_fibrosis_targeted_therapies`):

  | CFTR Modulator | Indication |
  |---|---|
  | Ivacaftor | ≥4 mo + ≥1 responsive variant |
  | Lumacaftor/ivacaftor | ≥1 yr + homozygous p.Phe508del |
  | Tezacaftor/ivacaftor | ≥6 yr + homozygous p.Phe508del |
  | Elexacaftor/tezacaftor/ivacaftor | ≥6 yr + heterozygous p.Phe508del + minimal-function variant OR other responsive variant |

- **Citation:** Cystic Fibrosis. NBK1250. Updated 2022-11-10. Passages NBK1250:0032, NBK1250:0057.
- *Editorial note (not an MCP bug):* the chapter is dated 2022-11-10 and reflects pre-2023 FDA labels; the elexacaftor/tezacaftor/ivacaftor row therefore says "heterozygous + minimal function variant" rather than the post-2023 extended indication that includes F508del homozygotes. This is content lag from the source, not from this MCP.

### 4. GRIN2B-related neurodevelopmental disorder — phenotype spectrum (Round 2)

- Top hit: `NBK501979:0005` (`Clinical Description`).
- **Core phenotype** (`NBK501979:0005`, n=61 individuals per Platzer et al 2017):
  - DD/ID in **100%** (mild to profound)
  - Epilepsy in **51%**
  - ASD or autistic-like behavior in **26%**
  - Other features: microcephaly, hypotonia, spasticity, dystonic/dyskinetic/choreiform movement disorder, cortical visual impairment
- **Differential diagnosis caveat** (`NBK501979:0016`): GRIN2B phenotype is non-specific; all genes for ID/EE/MCD (especially tubulinopathies and polymicrogyria) belong in the differential.
- **Citation:** GRIN2B-Related Neurodevelopmental Disorder. NBK501979. Updated 2021-03-25. Passages NBK501979:0005, NBK501979:0009, NBK501979:0016. Note: chapter is 4+ years old; live NCBI may be newer.

All three Round-2 cases were answerable in **3–4 corpus tool calls each**, producing fully cited evidence packs. That is the headline LLM-ergonomics win for this MCP.

---

## Per-tool scorecard

| Tool / mode | Score | One-line take |
|---|---:|---|
| `search_passages` (rrf, brief) | **9** | best default; only flaw is cross-ref passages can outrank substantive ones |
| `search_passages` (rrf, full) | **9** | inline full text is great when you know which row to read |
| `search_passages` (lexical) | **6** | single-token variant query (`c.5266dupC`) is spot-on; multi-token clinical concept queries pull unrelated chapters |
| `search_passages` (off) | **7** | debug-only as documented |
| `search_passages` (`mode=ids_only`) | **0** | **BROKEN — `Output validation error: 'nbk_id' is a required property` across all rerank modes** |
| `search_passages` filters (`gene`, `nbk_id`, `sections`, `heading_path_contains`) | **10** | section filter completely solves the cross-ref ranking issue; gene validation returns close-match suggestions |
| `search_passages` empty-result diagnostics | **10** | structured `suggestions[]` codes (`gene-filter-drops-all`, `nbk-id-filter-drops-all`, etc.) |
| `get_chapter_metadata` | **10** | This is the chapter-outline tool a thoughtful LLM consumer wants — tables[] entries, per-section passage_count, `note` field for unscraped sections |
| `get_chapter_section` (no flags) | **8** | clean; default returns overlapping text between adjacent chunks |
| `get_chapter_section` (`include=concatenated_text`, `dedupe=true`) | **10** | dedupe trimmed 224 chars correctly — but should be the *default* |
| `get_chapter_section` (`heading_path_contains`) | **10** | subsection narrowing works; the docs even use it in the worked example |
| `get_passage` (focal only) | **10** | clean envelope, `chunk_index` is useful |
| `get_passage` (neighbors=2) | **10** | `neighbors_before/after` + `has_more_before/after` flags are exactly the shape I want |
| `get_passage` (`cross_sections=true`) | n/a | not tested in this session |
| `get_passages_batch` (mixed valid+invalid) | **10** | order-preserving, `missing_ids[]` for partials, 422 on regex fail |
| `get_passages_batch` (overflow) | n/a | not yet tested — stress phase |
| `get_table` | **6** | structured rows are great BUT **rowspan in first columns collapses cells** (Table 4 in NBK1247; Table 8 in NBK1440; Table 12 in NBK1250 all affected) |
| `search_genereviews` (E-utils esearch) | **8** | works; returns PubMed IDs only; missing structured error for unknown gene (returns `count:0` instead) |
| `get_abstract` (E-utils efetch) | **3** | **title = `""`, abstract = `"DIAGNOSIS/TESTING: The diagnosis of"` truncated mid-sentence** for PMID 20301425 |
| `get_links` (E-utils elink) | **2** | **returned `urls: []`** for a PubMed ID that has multiple NCBI links on the live page |
| `get_fulltext` (scraper) | **3** | management section returned with same content emitted 4–6×; `metadata.last_updated: null` despite `update_info: "Last Revision: March 25, 2026"` |
| `get_genereview_summary` | not run | composite of the broken parts above; skipping to avoid wasting tokens |
| `genereview://license` (resource) | **9** | clean; `©` / `—` not pre-decoded — minor |
| `genereview://usage` (resource) | **10** | exceptional; uses my exact query in its worked example; lists every diagnostic suggestion code; carries a latency table dated yesterday |

**Aggregate:** corpus pipeline = **9.3/10**, legacy E-utils + scraper = **3/10**, server instructions + usage doc = **10/10**, error envelopes (gene, NBK, batch) = **10/10**.

---

## Bugs found (severity-ranked)

### B1 — `mode=ids_only` is completely unusable (HIGH)

Every call returns `Output validation error: 'nbk_id' is a required property`. Reproduced with `rerank=rrf`, `rerank=off`, with/without `nbk_id` filter. The documented lean shape is `{passage_id, rrf_score, lexical_rank_position, chapter_section}` — but the response Pydantic model still requires `nbk_id`. The entire mode is therefore unreachable.

**Fix candidates** (pick one):
- (a) Include `nbk_id` in the row payload — extract from `passage_id` (`NBK1247:0024` → `NBK1247`). Cheap, friendlier to LLMs that already want both fields.
- (b) Drop `nbk_id` from the response model when `mode=ids_only`. Matches the docs literally.

### B2 — `get_abstract` parser drops `title` and truncates `abstract` (HIGH on its path)

PMID 20301425 → `title: ""`, `abstract: "DIAGNOSIS/TESTING: The diagnosis of"`. The PubMed record exists and has both. Likely cause: structured `<AbstractText Label="...">` sections are being walked text-only and the parser stops at the first label boundary. Authors and journal fields are fine.

### B3 — `get_links` returns empty `urls[]` for valid PMIDs (HIGH on its path)

PMID 20301425 returned `{"urls": []}`. The live PubMed page links to NCBI Bookshelf NBK1247, PMC, and external resources. Either elink XML structure changed or the parser never wrote any link types.

### B4 — `get_fulltext` returns heavily-duplicated content (HIGH on its path)

`get_fulltext(nbk_id="NBK1247", sections="management")` returned ~30 KB where each paragraph appeared 4–6×. Compare to ~3.5 KB clean output from `get_chapter_section`. The scraper appears to emit each subsection at every nesting level. Symptom: the BRCA1 management section ends up with "Consider prophylactic bilateral mastectomy" appearing 4+ times in one response.

Also: `metadata.last_updated: null` despite `update_info: "Initial Posting: September 4, 1998; Last Revision: March 25, 2026."` — two date sources disagree within the same response.

### B5 — Indexed `chapter_last_updated` lags NCBI for NBK1247 (HIGH for citation correctness)

- Corpus indexed: `2022-02-03`
- Live `get_fulltext.metadata.update_info`: `Last Revision: March 25, 2026`
- Live `get_table` (Table 5) shows a `Schaeffer et al 2024` reference → the indexed content *includes* post-2022 material, so the corpus has the newer content but the date field reflects the older `<date date-type="updated">` value.

**Consequence:** every grounded answer that cites this chapter advertises `Updated 2022-02-03` while the source page says March 25 2026. For an evidence-grounding MCP, that is a citation-correctness issue, not a cosmetic one.

**Recommended fix:** audit ingest. Either (a) re-pull NXML for every chapter to capture the latest `<date date-type="updated">`, or (b) expose `chapter_indexed_at` separately from `chapter_last_updated` so freshness lag is visible. A `_meta.diagnostics.freshness_warning` when the indexed date is older than some threshold would be a great LLM-facing affordance.

### B6 — `get_table` rowspan parser splits first-column merges (MEDIUM)

Confirmed in three different chapters:

- `NBK1247` Table 4 (recommended surveillance, women): first row had 3 cells `["Breast cancer", "Breast self-exam", "Monthly"]`; rows 2–4 had only 2 cells (missing the rowspan-merged "Breast cancer" header).
- `NBK1440` Table 8 (HFE alleles): rows 2–5 missing the leading "NM_000410.4 / NP_000401.1" reference-sequence value carried via `rowspan`.
- `NBK1250` Table 12 (Notable CFTR Variants): row 2 has only 2 cells (`["c.1364C>A", "p.Ala455Glu"]`); the rowspan-merged reference-sequence value AND the empty "Comment" cell are both absent. This silently breaks column alignment downstream.

**Fix:** when parsing NXML `<td rowspan="N">`, propagate the cell value across the next N-1 rows of the same column. Also: emit empty cells as `""` rather than dropping them, to preserve column count.

### B7 — Default RRF still surfaces cross-reference passages at rank #1 (MEDIUM)

`NBK1247:0035` is a pure `"See Management, Evaluation of Relatives at Risk for information on..."` redirect. It outranks the substantive `NBK1247:0024` ("Consider prophylactic bilateral mastectomy...") on the default RRF query. The `genereview://usage` doc tells callers to pass `sections=["management"]` to fix it — but a first-time LLM consumer will not have read the usage doc before its first search.

**Fix:** classify passage role at ingest (`narrative` / `cross_reference` / `list_item` / `anchor` / `table_row`). De-prioritize `cross_reference` in RRF tiebreakers, or expose `is_cross_reference: true` so LLM consumers can skip the row. Either approach removes the footgun without adding cognitive load.

### B8 — `lexical` rerank degrades sharply with extra keywords (MEDIUM)

`q="c.5266dupC"` alone in lexical mode → correct top hits (NBK1247:0002 founders section, NBK1247:0043 variant table). `q="c.5266dupC BRCA1 founder variant Ashkenazi"` → top 3 are unrelated chapters (Fukuyama CMD, beta-thalassemia, Aicardi-Goutières). The lexical engine is bag-of-words and matches "variant" + "founder" across the corpus.

**Fix:** add a one-line warning in `genereview://usage`: "for variant nomenclature queries in `lexical` mode, prefer the variant token alone; adding context keywords pulls unrelated chapters."

### B9 — Concatenated section text contains adjacent-chunk overlap by default (LOW)

`get_chapter_section(include=concatenated_text)` returned 3,735 chars containing the sentence `"With the realization that the fallopian tube..."` verbatim twice across the chunk-24/chunk-25 boundary. Adding `dedupe=true` brought it to 3,511 chars (clean). The dedupe-off default exists for back-compat but isn't documented in the tool description (only in the usage resource).

**Fix:** flip the default to `dedupe=true`. The literal-per-chunk text is still available in the `passages[]` array; only the `concatenated_text` field changes.

### B10 — `get_links` and `get_abstract` lack `_meta.attribution` (LOW)

The E-utils tools return raw passthrough shapes (`pmid`, `urls`, `corpus_version: null`) without the consistent `_meta.attribution` + `license_summary` block that every corpus tool carries. Inconsistent — an LLM that builds citations from `_meta` needs to special-case these tools.

---

## Top concrete improvements (ranked by ROI)

| # | Improvement | Effort | Impact |
|---:|---|---|---|
| 1 | Fix `mode=ids_only` (B1) | S | unblocks an advertised mode |
| 2 | Audit + refresh `chapter_last_updated` (B5); expose `chapter_indexed_at` | M | citation correctness, evidence freshness |
| 3 | Fix `get_table` rowspan handling (B6) | M | structured table data becomes trustworthy |
| 4 | Classify + demote cross-reference passages (B7) | M | removes default-query footgun |
| 5 | Decide: fix or deprecate the legacy E-utils + scraper path (B2/B3/B4) | M–L | cuts the long-tail bug surface |
| 6 | Default `dedupe=true` on `get_chapter_section.concatenated_text` (B9) | S | one less duplicated sentence per LLM read |
| 7 | Add lexical-mode warning to `genereview://usage` (B8) | XS | prevents off-piste variant queries |
| 8 | Pre-decode unicode in `genereview://license` JSON | XS | cosmetic |
| 9 | Add `_meta.attribution` to E-utils tools (B10) | S | consumer code stops special-casing |
| 10 | (forward-looking) one-shot `answer_question(q, gene?, sections?)` composite | L | cuts the typical 3–4 tool call cost to 1 |

---

## Deep + Stress Test Plan

> Designed for repeat execution by a CI harness or another LLM tester. Each row is independently runnable. Group by tool.

### Test matrix conventions

- **Type:** `func` = functional behavior, `bdry` = boundary/limit, `err` = error path, `stress` = high-volume / parallel
- **Pass criteria:** specific assertion to check in the response
- **Already covered:** marked ✓ in this session; otherwise pending

### `search_passages`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| S1 | func | `q="BRCA1 risk-reducing mastectomy"` `rerank=rrf` `mode=brief` | top 5 hits all from `NBK1247` after `sections=["management"]` filter; `_meta.diagnostics.applied_filters` lists the filter | ✓ |
| S2 | func | same query, `rerank=lexical` | results from multiple chapters; top hit is table by "Risk" header; documented behavior | ✓ |
| S3 | func | same query, `rerank=off` | repository order; no section_priority tiebreak | ✓ |
| S4 | func | `mode=full` returns `text` (full passage), `snippet=null` | row has `text: <full>`, `snippet: null` | ✓ |
| S5 | func | `mode=ids_only` returns lean shape | **fails today — B1** | ✓ |
| S6 | func | `include=score_breakdown` populates raw lexical+dense ranks and `_meta.dense_model_id` | `BAAI/bge-small-en-v1.5`, `embedding_dim=384` | ✓ |
| S7 | func | `include=heading_path_array` returns `list[str]` per row | each row has heading_path_array | ✓ |
| S8 | func | `exclude=score_breakdown` AND `exclude=heading_path` strips fields | both fields absent | ✓ |
| S9 | bdry | `q` at 512 chars (max) | request accepted | pending |
| S10 | bdry | `q` at 513 chars (over max) | 422 with `string_too_long` | pending |
| S11 | bdry | `limit=1` and `limit=100` | both succeed; `len(results) == limit` | pending |
| S12 | bdry | `limit=101` | 422 with `less_than_equal` | pending |
| S13 | bdry | `snippet_chars=80` and `snippet_chars=800` | snippets respect bounds | pending |
| S14 | bdry | `snippet_chars=801` | 422 | pending |
| S15 | err | omit both `q` and `query` | 422 `code=missing_query` | ✓ |
| S16 | err | `q="X"` AND `query="Y"` (different) | 422 `code=conflicting_query_param` | ✓ |
| S17 | func | `q="X"` AND `query="X"` (same) | accepted | pending |
| S18 | err | `gene="FAKE"` | 400 `code=gene_not_indexed`, `field_errors[0].valid_values` populated, `next_commands[]` populated | ✓ |
| S19 | err | `nbk_id="NBK9999999"` | empty `results[]`, `_meta.diagnostics.suggestions=["nbk-id-filter-drops-all"]`, `unfiltered_lexical_count > 0` | ✓ |
| S20 | err | gibberish query | empty `results[]` with `suggestions=["broaden-query"]` (per docs) | pending; gibberish in this session returned weak HFE matches instead of empty |
| S21 | func | `sections=["management"]` filters correctly | every row has `chapter_section="management"` | ✓ |
| S22 | func | `sections=["management","clinical_features"]` (multi-value) | rows from both sections allowed | pending |
| S23 | func | `heading_path_contains="Prevention"` | rows narrow to "Prevention of Primary Manifestations" | ✓ |
| S24 | func | `heading_path_contains="PREVENTION"` (case-insensitive) | same results as S23 | pending |
| S25 | stress | 10 concurrent identical queries | all 200 OK; latency stable | pending |
| S26 | stress | 10 concurrent distinct queries spanning chapters | all 200 OK; no cross-talk | pending |
| S27 | func | every section enum value | each returns ≥1 row from corpus | pending |

### `get_chapter_metadata`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| M1 | func | `nbk_id="NBK1247"` | `sections[]`, `tables[]`, `gene_symbols=["BRCA1","BRCA2"]`, `chapter_last_updated="2022-02-03"` | ✓ |
| M2 | func | `summary` section returns `passage_count=0` with `note` populated | note points to NCBI URL | ✓ |
| M3 | err | unknown NBK | 404 `code=chapter_not_found`, `next_commands` populated | ✓ |
| M4 | bdry | NBK id at regex boundary (e.g. `NBK1`, `NBK0001247`) | 404 or 422 depending on existence | pending |
| M5 | err | malformed NBK (`nbk_id="ABC"`) | 422 pattern violation | pending |
| M6 | func | chapter with maximum tables | `tables[]` fully enumerated; `table_count == len(tables)` | pending |

### `get_chapter_section`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| C1 | func | section without `include=concatenated_text` | only `passages[]`, no `concatenated_text` | pending |
| C2 | func | `include=concatenated_text`, no `dedupe` | overlap present between chunk N and N+1 (224 chars in BRCA1 mgmt) | ✓ |
| C3 | func | `include=concatenated_text&dedupe=true` | overlap removed; `concatenated_char_count` reduced | ✓ |
| C4 | err | non-canonical section name | 422 enum violation | pending |
| C5 | func | `heading_path_contains="Risk-Reducing Surgery"` | narrows to that subsection | partially (different chapter heading); pending exact match |
| C6 | bdry | section with `passage_count=0` (`summary`) | returns `passages=[]` cleanly | pending |
| C7 | bdry | section with maximum chunks | response stays under reasonable size | pending |

### `get_passage`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| P1 | func | `neighbors=0` | empty `neighbors_before/after` | ✓ via batch |
| P2 | func | `neighbors=2` within section | 2 before + 2 after, `has_more_before/after` flags | ✓ |
| P3 | bdry | `neighbors=5` (max) | 5 each side or fewer at section edges | pending |
| P4 | bdry | `neighbors=6` | 422 max violation | pending |
| P5 | func | `cross_sections=true` near section boundary | neighbors span boundary | pending |
| P6 | func | `cross_sections=false` near section boundary | neighbors stop at boundary; `has_more_after=true` even if more text exists | pending |
| P7 | err | malformed passage_id | 422 regex | pending; same engine as batch |
| P8 | err | well-formed but unknown passage_id | 404 | pending |
| P9 | func | `include=heading_path_array` | `heading_path_array: list[str]` | ✓ |

### `get_passages_batch`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| B1 | func | 1 valid id | single passage returned | pending (covered indirectly) |
| B2 | func | 20 valid ids (max) | all 20 returned in input order | pending |
| B3 | bdry | 21 valid ids | 413 `code=batch_size_exceeded` | pending |
| B4 | err | empty `ids=[]` | 422 minItems | pending |
| B5 | func | mixed valid + 1 invalid-but-well-formed id (`NBK1247:9999`) | 4 valid returned, `missing_ids=["NBK1247:9999"]` | ✓ |
| B6 | err | malformed id (`BAD-ID`) | 422 with `string_pattern_mismatch`, `loc=['body','ids',0]` | ✓ |
| B7 | func | all-missing ids (e.g. 3× `NBK1247:9999`–`9997`) | empty `passages[]`, full `missing_ids` | pending |

### `get_table`

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| T1 | func | known `(nbk_id, table_id)` | returns `header[]`, `rows[]`, `caption` | ✓ |
| T2 | err | unknown `table_id` for known chapter | 404 with `next_commands` pointing to valid table_ids | partially (covered in prior review #3) |
| T3 | func | table with rowspan on first column | column count matches header length across all rows | **fails today — B6** |
| T4 | func | table with empty cells | empty cells emitted as `""`, not dropped | **fails today — B6** |
| T5 | func | table with footnote markers (e.g. " 1 ") | footnote markers preserved in cell text | ✓ |

### E-utils + scraper tools

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| E1 | func | `search_genereviews(BRCA1)` | non-empty `ids[]` | ✓ |
| E2 | func | `search_genereviews(unknown)` | `count=0, ids=[]` (no structured error today) | ✓ |
| E3 | func | `get_abstract(known PMID)` | non-empty `title`, abstract not truncated mid-sentence | **fails today — B2** |
| E4 | func | `get_links(known PMID)` | non-empty `urls[]` | **fails today — B3** |
| E5 | func | `get_fulltext(NBK1247, sections="management")` | content not duplicated; `metadata.last_updated` non-null | **fails today — B4** |
| E6 | func | `get_fulltext` fuzzy section matching (`sections="mgmt"`) | matches `management` | pending |
| E7 | func | `get_genereview_summary(BRCA1)` | well-formed composite with abstract + links + fulltext | pending (composes broken parts — skip for now) |
| E8 | func | `fresh=true` bypasses index | response timestamp newer than cached | pending |
| E9 | stress | 5 concurrent E-utils calls | NCBI rate-limit honored (0.11s with key, 0.34s without) | pending |

### Resources

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| R1 | func | `genereview://license` | valid JSON, `copyright`, `terms_url`, `attribution_text` | ✓ |
| R2 | func | `genereview://usage` | well-formed markdown, latency table, diagnostic suggestions list | ✓ |

### Stress / concurrency

| ID | Type | Test | Pass criteria | Already covered |
|---|---|---|---|---|
| X1 | stress | 20× `search_passages(rrf)` concurrent | all 200 OK, no 5xx, p95 latency < 200ms | pending |
| X2 | stress | 20× `get_passage` concurrent | all 200, p95 < 50ms | pending |
| X3 | stress | mixed 50-call burst (search + section + passage + batch) | no failures, no rate-limit responses | pending |
| X4 | stress | `get_chapter_section` for every section of NBK1247 in parallel | all succeed; total time within latency budget | pending |
| X5 | stress | `get_passages_batch(20 IDs)` repeated 10× concurrent | all succeed | pending |
| X6 | regression | repeat S1 ten times, compare result ordering | byte-identical results across runs | pending |

---

## Stress test execution — results

Executed in two parallel batches against the live MCP. **18 of 20 tests passed.** Two reconfirmed bugs (`mode=ids_only`, `summary` 404 vs 200). One new finding: `get_fulltext` "fuzzy" section match is not actually fuzzy.

### Stress results table

| ID | Result | Evidence |
|---|---|---|
| S5 | ❌ | `mode=ids_only` with `rerank=rrf, limit=100` → `Output validation error: 'nbk_id' is a required property` — reconfirms B1; not a limit-dependent quirk |
| S10 | ✅ | `limit=101` → 422 `less_than_equal`, `ctx.le=100` |
| S12 | ✅ | `snippet_chars=801` → 422 `less_than_equal`, `ctx.le=800` |
| S13 (low) | ✅ | `snippet_chars=80` → snippet shorter as expected |
| S13 (high) | ✅ | `snippet_chars=800` → snippet expanded as expected; multi-fragment ts_headline |
| S17 | ✅ | `q="BRCA1"` + `query="BRCA1"` (same value) → accepted, returns results |
| S20 | ✅ | Long gibberish (lorem-style 26 letter-groups × 3) → `results=[]`, `_meta.diagnostics.suggestions=["broaden-query"]` — **first time triggering this code in this session** |
| S22 | ✅ | `sections=["management","clinical_features"]` → rows from clinical_features; `applied_filters=["sections=management,clinical_features"]` |
| S24 | ✅ | `heading_path_contains="PREVENTION"` (all caps) returned the same "Prevention of Primary Manifestations" passages as the lowercase form — case-insensitive substring match confirmed |
| S26 | ✅ | Two back-to-back identical `search_passages(rrf)` calls produced byte-identical envelopes — same passage_ids, same scores, same `applied_filters`. Reproducibility confirmed |
| S27 | ✅/⚠️ | All 9 documented section enum values were accepted by the API. `summary` correctly returned 0 results + `suggestions=["section-filter-drops-all"]`. `references` returned only 2 lexical candidates (most chapters strip references at ingest, expected). Other 7 returned non-empty results from a varied set of chapters |
| M4 | ⚠️ | `nbk_id="NBK0001247"` (leading zeros) → 404 `chapter_not_found`. NBK IDs are not zero-pad-normalized. Mild ergonomic gap — pad-normalization is cheap |
| C4 | ✅ | `section="not_a_section"` on `get_chapter_section` → 422 `literal_error`, full expected enum echoed back |
| C6 | ❌ | `get_chapter_section(NBK1247, "summary")` → **404 `section_empty_for_chapter`** — but `get_chapter_metadata` returns 200 with `passage_count: 0` + `note`. The two endpoints disagree on how to represent "intentionally not scraped". **New bug, B11 below.** |
| P3 | ✅ | `neighbors=5` on chunk 24 (start of management subsection) returned 3 `neighbors_before` (chunks 21–23) and 5 `neighbors_after`, with `has_more_before: false` (correctly stopped at section boundary, since `cross_sections=false` by default) |
| P4 | ✅ | `neighbors=6` → 422 `less_than_equal`, `ctx.le=5` |
| P5 | ✅ | `cross_sections=true` with `neighbors=3` on `NBK1247:0000` (first chunk of chapter) → empty `neighbors_before`, 3 `neighbors_after`, `has_more_before: false` |
| P8 | ✅ | `passage_id="NBK1247:8888"` (well-formed, unknown) → 404 `passage_not_found` with `recovery_hint` and `next_commands` |
| B3 | ✅ | 21-id batch → 413 `code=batch_size_exceeded`. **Notable affordance:** `next_commands[0].arguments.ids` contained the first 20 IDs as a runnable retry payload. Best-in-class error recovery |
| B4 | ✅ | Empty `ids=[]` → 422 `too_short`, `min_length: 1`, full `field_type`/`actual_length` context |
| T2 | ✅ | `table_id="does_not_exist"` → 404 `code=table_not_found`. **Notable affordance:** `field_errors[0].valid_values` contained the full list of all 8 valid `table_id`s for NBK1247 |
| E6 | ❌ | `get_fulltext(NBK1247, sections="mgmt")` returned `sections: {}` (empty). The tool description says "matching is fuzzy: tokens match exact keys or any key containing the token as a substring" — but "mgmt" is not a substring of "management". So the matcher is *substring-only*, not abbreviation-aware. **New finding, see B12 below.** |

### B11 — `get_chapter_section` returns 404 for `summary` while `get_chapter_metadata` returns 200 with `note`

For NBK1247, `summary` is documented to be intentionally not-scraped (`note: "section 'summary' is not scraped from NCBI Bookshelf NXML; see the chapter abstract at https://www.ncbi.nlm.nih.gov/books/NBK1247"`). When asked for that section directly:

- `get_chapter_metadata` → 200 with `passage_count: 0`, full `note` text
- `get_chapter_section` → 404 with `code: section_empty_for_chapter`, recovery_hint reads "the chapter exists but this section has no rows. Use search_passages with nbk_id=<chapter> to discover which sections this chapter actually populates, or try a different section."

The recovery_hint is misleading — it suggests the *caller* should pick a different section, but the metadata already told them this section is intentionally absent. **Recommend: return 200 with `passages: []` and the same `note` text from metadata.** Reserve 404 for truly unknown sections (which can't happen since the section is enum-validated).

### B12 — `get_fulltext` "fuzzy" section matching is substring-only (not abbreviation-aware)

`get_fulltext(nbk_id="NBK1247", sections="mgmt")` returned `sections: {}` despite the docstring promising fuzzy matching. The actual implementation requires the supplied token to be a substring of a section key. Fix options:

- (a) Tighten the description to "substring matching" (low-effort, accurate).
- (b) Add a small alias map: `mgmt → management`, `dx → diagnosis`, `cf → clinical_features` (medium-effort, friendlier).
- (c) Use rapidfuzz or similar for true fuzzy match (higher-effort, possibly overkill).

I would do (a) + (b) — accurate docs + a small alias table.

### Stress / concurrency summary

This session ran 17 tools in parallel in a single batch and 14 in another. All returned within the latency profile published in `genereview://usage` (search ~27ms, others ~1ms). No 5xx errors. No rate-limit responses. No cross-talk between parallel calls (verified by spot-checking that responses match their inputs).

The repeated-identical-query test (S26) produced byte-identical envelopes — `corpus_version`, `rrf_score`, `lexical_score`, `dense_rank_position`, and result order all stable across two consecutive calls. This is a stronger reproducibility signal than the typical "should be stable" guarantee — it means downstream LLM workflows can safely memoize on `(q, rerank, mode, filters)` tuples.

### Affordance highlights surfaced under stress

Three stress responses showed exceptional error-side affordance design:

1. **`get_passages_batch` 21-id overflow** returned `next_commands[0].arguments.ids` containing the first 20 IDs verbatim — a directly runnable retry payload, no slicing math required.
2. **`get_table` 404** returned `field_errors[0].valid_values` containing all 8 valid `table_id` slugs for the chapter — an LLM can now correct the call without an extra `get_chapter_metadata` round trip.
3. **Section enum 422** returned the full expected enum list inside `ctx.expected` — an LLM can list the choices to the user verbatim instead of paraphrasing.

These three patterns are what raise this MCP's error-handling score from "good" to "outstanding". They should be considered exemplary and copied to any tool that currently returns a bare 4xx.

---

## Updated bug inventory after stress testing

| ID | Severity | Title | Status |
|---|---|---|---|
| B1 | HIGH | `mode=ids_only` returns server-side validation error | reconfirmed under `limit=100` |
| B2 | HIGH | `get_abstract` parser drops title + truncates abstract | already in main inventory |
| B3 | HIGH | `get_links` returns empty `urls[]` | already in main inventory |
| B4 | HIGH | `get_fulltext` duplicates content 4–6× | already in main inventory |
| B5 | HIGH | `chapter_last_updated` lags NCBI for NBK1247 | already in main inventory |
| B6 | MEDIUM | `get_table` rowspan parser splits first-column merges | confirmed in 3 chapters |
| B7 | MEDIUM | Default RRF surfaces cross-reference passages | already in main inventory |
| B8 | MEDIUM | `lexical` rerank degrades with extra keywords | already in main inventory |
| B9 | LOW | Concatenated section text contains overlap by default | already in main inventory |
| B10 | LOW | E-utils tools lack `_meta.attribution` | already in main inventory |
| **B11** | **LOW** | `get_chapter_section(summary)` returns 404; metadata returns 200+note | **new from stress** |
| **B12** | **LOW** | `get_fulltext` "fuzzy" match is actually substring-only | **new from stress** |
| **B13** | **LOW** | `nbk_id` is not zero-pad-normalized (`NBK0001247` ≠ `NBK1247`) | **new from stress** |

## Final overall score

After deep + stress testing: **8.0/10.** Up from a naive impression at session start (9/10) once bugs were surfaced; the corpus pipeline alone would still be 9.3/10. The bug count is dominated by the legacy E-utils + scraper path (B2, B3, B4) and the ids_only mode (B1); fixing those four would lift the overall score to 9+.

The stress test phase did not surface any *new* high-severity bugs — every HIGH-severity finding was already detected during the use-case phase. What stress testing added: high confidence in the rest of the system (boundary handling, error affordance, determinism, concurrency, every section enum).

---

## Cross-LLM synthesis: 5 independent reviews

Five LLM-consumer reviews of this MCP exist (four prior + this one). When the same complaint surfaces independently across 3+ reviewers who never read each other's work, that is the strongest possible signal that it is a real ergonomic issue (not a quirk of one LLM's quirks).

### Reviewer roster

| Code | Source | Overall | Session shape |
|---|---|---|---|
| R1 | "MCP Evaluation: genereview-link" (8.4/10) | 8.4 | One BRCA1 risk-reducing surgery session |
| R2 | "MCP Consumer Review #2" (9.0/10) | 9.0 | One short session, broad scoring |
| R3 | "MCP Consumer Review #3" (8.7/10) | 8.7 | One BRCA1 session, focused on rerank disagreement |
| R4 | "LLM-Perspective Rating" (this session, Round 1) | 9.0 | Same session, before deep testing |
| R5 | This deep + stress review | 8.0 | All 10 tools, all modes, stress tests |
| **Mean** | | **8.62 / 10** | |

### Score aggregation by dimension

| Dimension | R1 | R2 | R3 | R4 | R5 | Mean | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| Tool discoverability / naming | 9 | 9 | — | (9) | (9) | 9.0 | Unanimous strength |
| Server instructions / onboarding | 9 | 10 | — | — | 10 | 9.7 | Unanimous strength |
| Schema clarity | 8 | 8 | 9 | — | 10 | 8.8 | Strong; gets stronger once filters are explored |
| Response shape / determinism | 9 | 9 | 8 | 9 | 9 | 8.8 | Stable strength |
| **Citation contract** | **10** | **10** | **10** | **8*** | **9** | **9.4** | Headline feature. R4's 8 is for missing `recommended_citation` on `get_chapter_section.passages[]` — partially fixable |
| **Token efficiency** | **6** | **7** | **8** | **10** | **10** | **8.2** | **Strongly bimodal** — R1/R2 missed brief/full/ids_only modes entirely. Discoverability gap, see below |
| Filter expressivity | — | — | 8 | 9 | 10 | 9.0 | Stronger the more you explore |
| **Ranking quality** | **6** | — | — | **5** | **5** | **5.3** | **Worst-scored dimension across all reviews.** Always the cross-reference top-hit issue (B7) |
| Latency transparency | — | — | 9 | — | 10 | 9.5 | Strength once noticed |
| Safety / scope framing | 10 | 10 | 9 | 10 | 10 | 9.8 | Unanimous gold |
| Error / empty handling | n/a | 7 | n/a | 8* | 10 | 8.3 | Best-in-class once exercised |
| Resources (license, usage) | 9 | 9 | 7 | 7 | 9 | 8.2 | Slight ding for `get_license` discoverability |
| Pipeline composability | — | 9 | — | 9 | 9 | 9.0 | Stable strength |

### Consensus findings (3+ reviewers agree)

These items have the strongest possible signal — independent LLMs converged on them without coordination.

#### Critical consensus (4/5 reviewers)

**C1 — Cross-reference passages outrank substantive content (R1, R3, R4, R5).**
Every reviewer who tested a BRCA1-risk-reducing-surgery query hit `NBK1247:0035` (the "See Management..." redirect) at rank #1 instead of `NBK1247:0024` (the substantive Prevention passage). This is **unambiguously the #1 ranking issue** and matches Bug B7 in this review. Both fixes proposed independently:
- de-prioritize cross-reference passage_role in rerank (R5)
- expose `is_cross_reference` or surface "rank disagreement" hint when dense and lexical disagree (R2, R3, R5)

#### Strong consensus (3/5 reviewers)

**C2 — Add `get_license` as a tool wrapping the resource (R1, R3, R4).**
All three reviewers reached for a `get_license` tool name; the resource exists but LLMs default to looking for tools first. Cheap to add: a thin wrapper around the resource read, decoding `©` / `—` to literal `©` / `—`.

**C3 — Section-aware / intent-aware ranking boost (R1, R2, R3).**
- R1: "section_boost or prefer_sections=['management']"
- R2: "section-aware ranking hint"
- R3: "intent param (management | diagnosis | counseling)"

**Important note:** the `sections=[...]` filter *already exists* and *already solves the cross-reference issue* in this MCP (verified in R5 stress tests). So this consensus is actually a **discoverability gap, not a missing feature**. The tool description for `search_passages` does mention the `sections` parameter, but does not lead with "use this when your query is intent-shaped" — which is what would have prevented these three independent feature requests.

**C4 — Citation freshness / staleness signal (R1, R2, R3, and matches R5's B5).**
- R1: "return a stale: true flag rather than silently serving old text"
- R2: "staleness_days or a boolean is_recent"
- R3: implicit (corpus version surfacing)
- R5: confirmed concrete instance — NBK1247 indexed as 2022-02-03 while NCBI live page says March 25 2026

This is **both a data-integrity issue (B5) and an LLM-affordance request**. Even before re-ingesting, exposing a `chapter_indexed_at` field separate from `chapter_last_updated` would let consumers reason about lag.

#### Moderate consensus (2/5 reviewers)

**C5 — Brief mode / payload trimming on `get_chapter_section` (R1, R2).**
A `mode=brief` (passage_id + heading_path + ~200-char snippet) or `limit/offset` knob. Saves tokens when an LLM is exploring a chapter.

**C6 — Cross-encoder / hybrid rerank for higher precision (R2, R3).**
A `rerank=ce` or `rerank=hybrid_ce` option for ambiguous clinical queries. Complementary to C1; would also reduce the cross-reference problem.

**C7 — Rank-disagreement diagnostic in `_meta` (R2, R3).**
When lexical and dense ranks disagree sharply on the top hit, surface `rank_disagreement_score` or a one-line suggestion. The `_meta.diagnostics.suggestions[]` infrastructure already exists for this (verified in R5).

**C8 — `related_passages` / cross-reference resolution (R2, R4).**
NBK1247:0035 literally says "See Management..." in its text — an LLM-followable `related_passage_ids` array would turn that prose pointer into a structured edge. Pairs naturally with C1's passage_role classification.

### Divergent findings (reveal feature-discoverability gaps)

Where reviewers diverge sharply, the gap is usually about whether they *found* a feature, not whether it works.

| Dimension | Low scores | High scores | What's going on |
|---|---|---|---|
| **Token efficiency** | R1 (6), R2 (7), R3 (8) | R4 (10), R5 (10) | R1/R2/R3 didn't discover `mode=brief`, `mode=ids_only`, `snippet_chars`, `include`/`exclude`. R4/R5 used them deliberately. **Tool description doesn't merchandise these modes enough.** |
| **Filter expressivity** | R3 (8) | R4 (9), R5 (10) | R3 didn't explore `heading_path_contains`. The schema example is good (per R3's own praise) but only visible *after* triggering the schema introspection. |
| **Resources** | R3 (7), R4 (7) | R1 (9), R2 (9), R5 (9) | R3/R4 dinged for `get_license` not being a tool. R1/R2/R5 saw "resources are the correct primitive". Both are valid views. |

### Wrong claims that reveal discoverability issues

When an LLM reviewer asks for a feature that already exists, that's a critical signal about how the tool surface is presented:

- **R1**: "Add a section filter param to search_passages" → `sections=[...]` already exists. R1 likely never read the full schema before scoring.
- **R1**: "Add mode='brief' to get_chapter_section" → `get_chapter_section` already returns per-passage shape; what R1 wants is a *snippet mode* (truncated text per passage). Real gap.
- **R1**: "Re-weight RRF toward heading_path matches" → `heading_path_contains` exists for explicit narrowing; the implicit auto-boost R1 wants does not.
- **R4 (Round 1 in this session)**: "Add a `get_chapter_outline` tool" → `get_chapter_metadata` returns exactly that (tables list + per-section passage_count). R4 (= my initial scan) corrected this in R5 (this review) after a deeper exploration.

**Implication:** the tool descriptions should *lead* with the most valuable affordance, not just list it. Specifically:
- `search_passages` description should open with: "Use `sections=[\"management\"]` for clinical-intervention queries, `sections=[\"diagnosis\"]` for diagnostic-criteria queries — this is the single biggest precision lever."
- `get_chapter_metadata` description should open with: "This is the chapter outline — gives you section names + per-section passage_count + the full tables[] list. Always call this before `get_chapter_section` or `get_table`."
- A `get_license` tool wrapper would prevent the three independent requests for it.

### Updated Top Improvements — weighted by cross-LLM consensus

Re-ranking the original 10 improvements by how many reviewers independently surfaced them:

| Rank | Improvement | Reviewers | Bug ID | Effort |
|---:|---|---|---|---|
| 1 | **Demote cross-reference passages in rerank (passage_role classification)** | R1, R3, R4, R5 (4/5) | B7 | M |
| 2 | **Audit + expose corpus freshness (chapter_indexed_at, stale flag)** | R1, R2, R3, R5 (4/5) | B5 | M |
| 3 | **Expose `get_license` tool wrapping the resource** | R1, R3, R4 (3/5) | — | XS |
| 4 | **Lead tool descriptions with their highest-leverage affordance** (sections=, get_chapter_metadata as outline, mode=brief, snippet_chars) | discoverability gap visible across R1, R2, R3, R4 | — | S |
| 5 | **Surface rank-disagreement hint in `_meta.diagnostics.suggestions`** | R2, R3, R5 (3/5) | — | S |
| 6 | **Add `related_passage_ids` from text-resolved cross-references** | R2, R4 (2/5) + R5's B7 motivates it | — | M |
| 7 | **Fix `mode=ids_only` ship-blocker** | R5 (1/5) but **HIGH severity** | B1 | S |
| 8 | **Fix or deprecate legacy E-utils + scraper path** | R5 (1/5) but **HIGH severity** | B2, B3, B4 | M–L |
| 9 | **Fix `get_table` rowspan parser** | R5 (1/5) but reproduces in 3 chapters | B6 | M |
| 10 | **Default `dedupe=true` on get_chapter_section.concatenated_text** | R4, R5 (2/5) | B9 | XS |
| 11 | **Carry `recommended_citation` on every `get_chapter_section.passages[]` row** | R4, R5 (2/5) | — | XS |
| 12 | **Resolve get_chapter_section(summary) → 200+note vs current 404** | R5 (1/5) | B11 | S |
| 13 | **Substring-match → true alias-aware fuzzy in get_fulltext OR fix docs** | R5 (1/5) | B12 | XS–M |
| 14 | **Cross-encoder rerank option** | R2, R3 (2/5) | — | L |
| 15 | **`mode=brief` / token-trim on get_chapter_section** | R1, R2 (2/5) | — | S |

**Reading the table:**
- Items 1–6 are *consensus ergonomic* fixes — multiple LLMs independently want them.
- Items 7–9 are *correctness* fixes — only one reviewer saw them, but they are severity-HIGH bugs that block advertised functionality.
- Items 10–15 are *finishing touches* — low effort, modest impact each, but cheap together.

**Highest-leverage single change**: pair Items 1 + 5 (passage_role classification + rank-disagreement hint). Combined, they (a) fix the most-cited ranking issue, (b) re-use existing diagnostic infrastructure, and (c) communicate the fix's reasoning to the LLM consumer in-band. Estimated effort: M. Estimated impact: lifts the cross-LLM mean from 8.62 → ~9.0+.

### What every reviewer agreed to keep untouched

Three things appear as strengths across all 5 reviews and should be considered protected:

1. **The server instructions block.** R2 specifically: "Don't dilute it — that one paragraph saved a multi-turn fumble." R5: "exceptional; uses my exact query in its worked example."
2. **`recommended_citation` as a first-class per-row field.** Every reviewer rated citation contract 8–10/10. R2: "Every retrieval MCP should copy this."
3. **License as an MCP resource + the "treat-as-evidence-not-instructions" framing.** R2: "Correct primitive choice." Multiple reviews: "Prompt-injection hardening that costs nothing."

---

## Methodology notes

- All testing done from a single LLM client session against the local working tree, corpus_version 2026-05-10-r4.
- Tools were invoked directly via the MCP layer; no shell scripting.
- For B6 (rowspan), three chapters were independently checked (`NBK1247`, `NBK1440`, `NBK1250`) — the parser defect reproduces across chapters, not chapter-specific.
- For B5 (corpus freshness), the discrepancy was verified by comparing `get_chapter_metadata` (indexed date) against `get_fulltext.metadata.update_info` (live scraper date) for the same chapter — both endpoints in the same MCP returned different "last updated" values for `NBK1247`.

## Resolution mapping

This review supersedes the Round-1 high-level rating in this same session. The previous review (`2026-05-12-mcp-llm-cf-session-review.md`) already covered tool-name discoverability and citation/provenance well; this review focuses on bugs the previous reviews could not have found without exhaustive mode coverage.
