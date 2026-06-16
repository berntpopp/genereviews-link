# Local Biomedical Annotation Tool Probe

**Date:** 2026-05-14
**Branch:** `spike/pubtator-local-annotation`

## Goal

Evaluate local entity annotation options for GeneReviews passage indexing, focused
on the C-gamma failures:

- HFE C282Y / p.Cys282Tyr / c.845G>A variant normalization
- GRIN2B as a gene/chapter anchor
- CFTR p.Phe508del and modulator-therapy table retrieval

This is research/probing only. No production code was changed.

## Local Inventory

Conda environments:

- `pt-spike`: has `torch`, `transformers`, `gliner`, `spacy`, and
  `huggingface_hub`.
- `varlens-annotate`: does not have the relevant NLP/model packages.
- Current project `uv` environment has `torch`, `transformers`,
  `sentence_transformers`, and `huggingface_hub`; it does not have `gliner`
  unless launched with `uv run --with gliner`.

Local relevant checkouts/caches:

- `/home/bernt-popp/development/pubtator-link` exists and contains entity
  matching, annotation, and variant-evidence services.
- Hugging Face cache is already large (`~72G`) and includes:
  - `Brizape/tmvar-PubMedBert-finetuned-24-02` (`~832M` cached)
  - `Ihor/gliner-biomed-base-v1.0` (`~756M`)
  - `Ihor/gliner-biomed-small-v1.0` (`~594M`)
  - `anthonyyazdaniml/gliner-biomed-bi-large-v1.0-disease-chemical-gene-variant-species-cellline-ner`
    (`~2.2G`)
  - `SIRIS-Lab/AIObioEnts-AnatEM-pubmedbert-full` (`~417M`)
  - `d4data/biomedical-ner-all` (`~255M`)
  - `ncbi/MedCPT-Cross-Encoder`
  - `pritamdeka/S-PubMedBERT-MS-MARCO`

Hardware observed:

- RAM: 60 GiB total, 36 GiB available during probe.
- GPU: RTX 5090, 32 GiB VRAM, ~31 GiB free during probe.
- Disk: 2.7 TiB free.

Important local environment caveat:

- `pt-spike` has an older PyTorch build that does not support RTX 5090
  `sm_120`; CUDA inference fails there with `no kernel image is available for
  execution on the device`.
- The current project `uv` environment ran the same transformer probe on GPU.

## PubTator 3.0 Stack Feasibility

PubTator 3.0 is an important reference for entity IDs and tool composition,
but it is not the best practical first stack for this repo. The full stack is
too large, too old-dependency-heavy, and too operationally awkward to treat as
the default implementation path without a dedicated offline containerization
pass.

What it provides conceptually:

- AIONER detects entity spans.
- GNorm2 normalizes gene/species mentions.
- tmVar3 normalizes variants.
- NLM-Chem normalizes chemicals.
- TaggerOne normalizes diseases and cell lines.

The Zenodo PubTator 3.0 archive is practical only as an offline annotator, not
as part of the VPS/runtime image:

- Full Zenodo bundle: 12.8 GB.
- AIONER: 5.9 GB.
- GNorm2: 4.6 GB.
- NLM-Chem: 714 MB.
- TaggerOne: 217 MB.
- tmVar3 code: 369 MB.
- tmVar3 database tarball from NCBI FTP: **106.6 GB**.

That last point is the main correction from earlier notes: tmVar3 code is
small enough, but production-grade local tmVar3 normalization needs a very
large database.

Recommendation: treat PubTator 3.0 as the gold/reference architecture and a
possible later offline annotator, not as the first implementation target. The
more realistic near-term path is cached local span models plus deterministic
normalization/gazetteer lookup.

## Existing Lookup Smoke Test

I used the already-available PubTator-Link MCP entity lookup as a quick smoke
test for known IDs. This is not a production baseline and not evidence that
GeneReview-Link should depend on PubTator-Link at runtime.

The lookup resolved these known query anchors:

- `HFE C282Y p.Cys282Tyr c.845G>A rs1800562` resolves to a LitVar variant
  record with db id `rs1800562##`.
- `GRIN2B` resolves first to NCBI Gene `24410`.
- `CFTR` resolves first to NCBI Gene `1080`.
- `CFTR p.Phe508del F508del` resolves first to a LitVar variant record with
  db id `rs113993960##`.
- `elexacaftor tezacaftor ivacaftor` resolves to MeSH `C000706587`.
- `cystic fibrosis` resolves to MeSH `D003550`.

The raw text annotation endpoint failed in this session with
`Failed to get session ID`, while the diagnostics endpoint reported the server
database as ready/current.

Conclusion: PubTator-Link was useful only as a one-off sanity check that the
known benchmark anchors map to plausible public identifiers. It should not be
treated as the annotation pipeline, the normalization backend, or a required
dependency for C-gamma.

## Hugging Face Model Probe

### `Brizape/tmvar-PubMedBert-finetuned-24-02`

Best local lightweight variant mention detector in the cache.

Probe command:

```bash
uv run python - <<'PY'
from transformers import pipeline
ner = pipeline(
    "ner",
    model="Brizape/tmvar-PubMedBert-finetuned-24-02",
    aggregation_strategy="simple",
    device=0,
)
PY
```

Observed results:

- HFE query:
  - `C282Y` -> `ProteinMutation`, 0.989
  - `p.Cys282Tyr` -> `ProteinMutation`, 0.996
  - `c.845G>A` -> `DNAMutation`, 0.998
  - `rs1800562` -> `SNP`, 0.981
- CFTR query:
  - `p.Phe508del` -> `ProteinMutation`, 0.994
  - `F508del` -> `ProteinMutation`, 0.985
- BRCA1 query:
  - `c.5266dupC` was detected only partially as `c. 5266du`; this model needs
    post-processing or a better tokenizer/threshold strategy for some HGVS
    strings.

Recommendation: use this as a cheap variant-span detector candidate, but not
as the sole normalizer. Pair it with PubTator/LitVar/dbSNP/ClinGen lookup.

### GLiNER-BioMed

`Ihor/gliner-biomed-base-v1.0` was the best general local NER probe.

Observed results:

- HFE query:
  - `p.Cys282Tyr`, `c.845G>A`, and `rs1800562` detected as variants.
  - `hereditary hemochromatosis` detected as disease.
  - `HFE C282Y` sometimes merged as gene or variant depending on labels.
- GRIN2B query:
  - `GRIN2B-related` detected as gene.
  - `neurodevelopmental disorder` detected as disease.
  - `developmental delay` detected as phenotype.
  - `epilepsy` detected as disease.
- CFTR query:
  - With labels `gene`, `variant`, `disease`, `drug`, it detected
    `CFTR p.Phe508del` as variant, the modulator combination as drug, and
    `cystic fibrosis` as disease.

`Ihor/gliner-biomed-small-v1.0` was usable but a little less stable; it
mis-labeled `Phe508del` as chemical in one probe.

The specialized
`anthonyyazdaniml/gliner-biomed-bi-large-v1.0-disease-chemical-gene-variant-species-cellline-ner`
model worked well for HFE and CFTR but missed standalone `GRIN2B` as a gene in
`GRIN2B-related neurodevelopmental disorder`; it treated the full phrase as
disease/phenotype. It also missed `p.Phe508del` in the CFTR sentence at the
tested threshold.

Recommendation: if using GLiNER locally, start with
`Ihor/gliner-biomed-base-v1.0` plus label/prompt tuning and a deterministic
post-pass that splits gene+variant compounds.

### Generic HF Biomedical NER

`d4data/biomedical-ner-all` and
`SIRIS-Lab/AIObioEnts-AnatEM-pubmedbert-full` were weak for the GeneReviews
anchor strings:

- `d4data/biomedical-ner-all` mislabeled gene/variant substrings as generic
  diagnostic procedure/medication fragments.
- `SIRIS-Lab/AIObioEnts-AnatEM-pubmedbert-full` returned sparse and incorrect
  labels (`HFE`/`CFTR` as organ).

Recommendation: do not use these as primary annotators for the C-gamma entity
layer.

## BERN2

BERN2 is relevant but operationally heavy:

- Local install expects Python 3.7-era dependencies and a separate resource
  download.
- Its README states 70 GB free disk for resources and minimum GPU run
  requirement of 63.5 GB RAM plus 5.05 GB GPU.
- That is feasible on the RTX workstation but not suitable for the VPS or
  runtime image.

Recommendation: keep BERN2 as a comparison baseline if needed, not as the
first C-gamma implementation path.

## Working Recommendation

Use a two-stage local annotation strategy:

1. **Short-term prototype, no huge downloads**
   - Use `Brizape/tmvar-PubMedBert-finetuned-24-02` for variant spans.
   - Use `Ihor/gliner-biomed-base-v1.0` for gene/disease/drug/phenotype spans.
   - Normalize query-time anchors with PubTator-Link/entity lookup, plus a
     small local gazetteer for HGNC, MeSH/HPO, and common variant aliases.

2. **Full offline indexer**
   - Evaluate PubTator 3.0/tmVar3 with the full 106 GB database only on the
     workstation.
   - Emit compact `passage_entities` / `chapter_entities` artifacts.
   - Keep the VPS runtime to gazetteer lookup plus SQL overlap scoring.

3. **Do not put model inference on the hot path**
   - GLiNER and tmVar-PubMedBERT are acceptable offline or build-time tools.
   - Query-time should use deterministic gazetteer matching for latency and
     reproducibility.

## Next Concrete Test

Build a tiny probe harness on this branch that:

- Samples 20-50 locked ranking-bench queries/passages.
- Runs:
  - Local deterministic/gazetteer lookup for query anchors.
  - GLiNER-BioMed base span detection.
  - tmVar-PubMedBERT variant span detection.
- Writes JSONL with spans, labels, normalized IDs when available, latency, and
  model/tool versions.
- Scores coverage of the three C-gamma misses before implementing database
  schema or retrieval changes.

## 2026-05-23 Repo Status Update

Senior MCP/LLM engineering status check against current `main`
(`25744d5`, after group-A API reliability work) shows that this probe is still
research evidence, not implemented product behavior. This file and the paired
RTX bake-off review are currently local/untracked review artifacts in the
working tree.

Current implementation status:

- **Probe harness:** missing. There is no checked-in script that runs GLiNER,
  tmVar-PubMedBERT, HunFlair2/linkers, deterministic lookup, and JSONL scoring
  over bench queries/passages.
- **Model wrappers/dependencies:** partially present only at the generic
  Transformers layer. `pyproject.toml` includes `transformers` and
  `sentence-transformers`, but not `gliner`, `flair`/HunFlair2, scispaCy, or an
  Aho-Corasick gazetteer package. No tmVar/GLiNER/HunFlair wrapper module exists.
- **Entity runtime:** missing. There is no `genereview_link/entities/` package,
  no query gazetteer, and no deterministic normalized entity extraction from
  query text.
- **Entity storage:** missing. Data migrations define chapters, passages,
  embeddings, table metadata, and `passage_role`, but no `passage_entities` or
  `chapter_entities` tables/materialized views.
- **Entity overlap ranking:** missing. `ScoreBreakdown` exposes lexical, dense,
  RRF, and role/intent fields only. Search does not join passage-level entities
  or add an `entity_overlap_boost`.
- **PubTator-Link runtime dependency:** correctly avoided. Current repo only
  references PubTator-Link as source/inspiration comments; it is not imported as
  a runtime dependency.
- **Adjacent ranking work:** implemented. Current search has parallel lexical +
  dense RRF, role-aware scoring, and table passage roles. This supports the
  general "no biomedical model on the hot path" constraint, but it is not the
  entity layer described here.
- **CFTR table-vs-narrative mitigation:** partial. Table bodies are classified
  as `table_body`, but the multiplier is neutral (`1.0`) and there is no
  property-intent boost for tokens such as `indication`, `frequency`, `dose`,
  `onset`, `penetrance`, or `carrier`.
- **GRIN2B structural anchor:** partial only through explicit `gene=` filters.
  Query text is not auto-tagged into a chapter gene anchor, so the C-gamma class
  of miss is still structurally possible.
- **Benchmark apparatus:** present. The locked ranking bench includes the HFE,
  CFTR, and GRIN2B cases. Saved C-alpha results show RRF at P@1 `0.408`,
  Recall@5 `0.826`, and no RRF exact-symbol regressions, but this is not
  evidence that the entity recommendations were implemented.

Updated engineering interpretation:

- Treat the May 13 C-gamma PubTator-3 design as architecturally directionally
  right but operationally superseded by this May 14 probe. The full PubTator-3
  stack remains a reference/baseline, not the first implementation target.
- The first practical path is still a hybrid offline annotator:
  GLiNER-BioMed for broad spans, tmVar-PubMedBERT plus HGVS regex repair for
  variants, HunFlair2/linkers where normalized gene/disease/chemical IDs are
  reliable, and deterministic gazetteers for query-time use.
- It is no longer necessary to block all schema design on model bake-off output.
  The minimal storage shape is stable across annotators: canonical entity rows
  plus passage-level matches with entity type, normalized ID, surface form,
  offsets, confidence, source, and source version. The model choice can remain
  pluggable behind that interface.

## Lessons From `../mdr-mcp`

The `mdr-mcp` repo has portable architecture patterns that should inform the
GeneReview-Link entity layer:

- **Schema/provenance pattern:** `regulatory_entities` plus `passage_entities`
  stores canonical IDs, entity types, aliases, offsets, confidence, source, and
  source version. GeneReview-Link should adapt this as biomedical
  `biomedical_entities` / `passage_entities`, with optional `chapter_entities`
  rollup for chapter-level gene and disease anchors.
- **Gazetteer pattern:** source-attributed seeds, Aho-Corasick fast path, regex
  fallback, clean-boundary checks, and longest-match dedupe. Biomedical sources
  should be HGNC/NCBI Gene, MeSH, HPO, RxNorm-like drug aliases where available,
  and dbSNP/LitVar/ClinGen-derived variant aliases.
- **Offline/online separation:** MDR's ingestion docs explicitly separate heavy
  offline extraction/annotation/embedding from read-only serving. That matches
  this probe's "no model inference on the hot path" constraint.
- **Replacement flow:** MDR prepares annotations offline, replaces rows by
  source/source version, and keeps provenance. GeneReview-Link should follow that
  shape so an annotator rerun does not require ad hoc destructive cache work.
- **Parent/leaf display pattern:** MDR searches leaf passages but can collapse
  to parent passages for display. GeneReview-Link can consider an analogous
  section/table grouping later, especially for CFTR table-vs-narrative cases.
  This should not be conflated with the entity layer's first milestone.

Domain-specific MDR vocabulary, regulatory reference parsing, and clause
resolution are not portable. The reusable pieces are the storage, provenance,
gazetteer, and retrieval-pipeline shapes.

## Revised Next Steps

1. Create a tracked C-gamma hybrid annotation plan that explicitly supersedes
   the PubTator-3-first assumption in the May 13 spec.
2. Add the smallest useful probe harness:
   - input: locked ranking bench queries, the three marquee passages, and 50
     random corpus passages;
   - output: JSONL rows with text ID, model/tool versions, spans, labels,
     normalized IDs when available, latency, and C-gamma anchor recovery flags;
   - execution: offline only, with optional dependencies supplied through
     `uv run --with ...` until the winning stack is chosen.
3. In parallel, design the stable entity schema/gazetteer interface using the
   `mdr-mcp` provenance pattern. Keep annotator choice behind a loader contract.
4. Investigate production-grade query-time deterministic gazetteer extraction
   from authoritative sources before passage annotation integration. Do not
   start with a hand-curated HFE/CFTR/GRIN2B seed list as implementation data;
   use those anchors as benchmark probes and regression tests only.
5. Only after the probe and query gazetteer pass the three C-gamma anchors, wire
   `entity_overlap_boost` into RRF and rerun `make bench-ranking` against the
   locked fixture.
