# PubTator-Style Annotation Alternatives RTX Bake-Off

**Date:** 2026-05-14
**Branch:** `spike/pubtator-local-annotation`
**Machine:** RTX 5090, 32 GB VRAM, PyTorch `2.11.0+cu130`

## Question

Find practical alternatives to a full PubTator 3.0 local stack for annotating
GeneReviews passages with PubTator-style biomedical entities:

- genes/proteins
- variants
- diseases/phenotypes
- chemicals/drugs
- normalized IDs where feasible

The target is offline corpus annotation plus a tiny runtime/query gazetteer, not
model inference on the VPS hot path.

## Test Sentences

I tested five GeneReviews-like anchor strings:

1. `HFE C282Y (p.Cys282Tyr, c.845G>A, rs1800562) allele frequency in hereditary hemochromatosis.`
2. `GRIN2B-related neurodevelopmental disorder phenotype spectrum with developmental delay and epilepsy.`
3. `CFTR p.Phe508del and F508del modulator therapy with elexacaftor tezacaftor ivacaftor for cystic fibrosis.`
4. `BRCA1 c.5266dupC founder variant in Ashkenazi populations and hereditary breast and ovarian cancer.`
5. `PTEN hamartoma tumor syndrome surveillance includes breast, thyroid, endometrial, renal, and colon cancer screening.`

Raw outputs were written to `/tmp/genereviews-annotation-bakeoff/`.

I also ran a broader concept pass for diseases, phenotypes/symptoms,
chemicals/drugs, anatomy-like terms, lab-test-like terms, species, and cell
lines. Raw output:
`/tmp/genereviews-annotation-bakeoff/broader_concepts_results.txt`.

## Candidates Researched

### HunFlair2

Why it matters:

- Current non-PubTator biomedical NER/NEN system with explicit gene, chemical,
  disease, species, and cell-line support.
- Its docs present it as a biomedical tagger and linker, with gene/disease/
  species linkers and preprocessed knowledge bases.
- It reports better cross-corpus end-to-end NER/NEN performance than BENT,
  BERN2, PubTator Central, SciSpacy, and HunFlair in the cited evaluation.

Sources:

- HunFlair2 docs: https://flairnlp.github.io/flair/master/tutorial/tutorial-hunflair2/overview.html
- HunFlair HF models: https://huggingface.co/hunflair/models

### GLiNER-BioMed

Why it matters:

- Open biomedical NER model family that supports arbitrary labels at inference.
- Useful for quickly trying GeneReviews-specific labels without training.
- The GLiNER-BioMed paper reports a 5.96 F1 improvement over the strongest
  baseline in zero/few-shot biomedical NER settings.

Sources:

- Paper: https://arxiv.org/abs/2504.00676
- Specialized model card: https://huggingface.co/anthonyyazdaniml/gliner-biomed-large-v1.0-disease-chemical-gene-variant-species-cellline-ner

### tmVar-like Variant Taggers

Why it matters:

- Variants are the hardest C-gamma anchor; generic NER tools often miss or
  mangle HGVS strings.
- Full tmVar3 is large operationally because its normalization DB is huge.
- A local PubMedBERT token classifier for tmVar-style spans is already cached.

Tested model:

- `Brizape/tmvar-PubMedBert-finetuned-24-02`

### scispaCy

Why it matters:

- Fast CPU biomedical entity detector and UMLS linker option.
- Good for broad term spotting, weak for typed PubTator-style output unless
  combined with semantic type filters/linkers.
- Not an RTX candidate; tested only as a CPU reference.

Sources:

- scispaCy docs: https://allenai.github.io/scispacy/
- scispaCy GitHub: https://github.com/allenai/scispacy

### OpenMed NER

Why it matters:

- Newer split-domain Hugging Face model family with high download counts.
- Domain-specific models exist for disease/pathology, pharma/chemical, and
  genomic concepts.

Source:

- OpenMed NER paper: https://arxiv.org/abs/2508.01630

## RTX Results

### Best General Span Model: specialized GLiNER-BioMed uni-encoder

Model:

`anthonyyazdaniml/gliner-biomed-large-v1.0-disease-chemical-gene-variant-species-cellline-ner`

Cache size: `~1.8G`

Load: `3.33s`

Per-sentence inference: roughly `0.012-0.024s` after load.

Strong hits:

- `HFE` as gene, `C282Y`, `p.Cys282Tyr`, `c.845G>A`, `rs1800562` as sequence
  variants, `hereditary hemochromatosis` as disease/phenotype.
- `GRIN2B-related` as gene, `neurodevelopmental disorder`, `developmental
  delay`, and `epilepsy` as disease/phenotype.
- `CFTR` as gene, `p.Phe508del` and `F508del` as sequence variants,
  `cystic fibrosis` as disease.
- `BRCA1` as gene and `c.5266dupC` as sequence variant.
- `PTEN` as gene and `hamartoma tumor syndrome` as disease/phenotype.

Weakness:

- Chemical/drug grouping was imperfect. It split `elexacaftor` and `ivacaftor`
  and missed/under-scored `tezacaftor` in one pass.
- It provides spans and types, not normalized IDs.

Broader concept pass:

- Diseases: strong on `Wilson disease`, `PTEN hamartoma tumor syndrome`,
  `Mitochondrial DNA depletion syndrome`, `cystic fibrosis`,
  `Fragile X syndrome`, `autism spectrum disorder`, and
  `Ataxia-telangiectasia`.
- Symptoms/phenotypes: strong on `hypotonia`, `lactic acidosis`, `seizures`,
  `liver failure`, `intellectual disability`, `macroorchidism`,
  `behavioral abnormalities`, `cerebellar ataxia`, and `telangiectasias`.
- Chemicals/drugs: strong on `penicillamine`, `trientine`, `zinc`,
  `elexacaftor`, `tezacaftor`, `ivacaftor`, `dornase alfa`, and
  `azithromycin`.
- Anatomy-like labels can be elicited by adding labels such as
  `anatomical site`; it tagged `breast`, `thyroid`, `endometrial`, and
  `renal` as anatomy-like spans in the PTEN surveillance sentence.
- Species/cell line: clean on `A549` as cell line and `Mus musculus` as
  species.
- Lab marker caveat: `alpha-fetoprotein` was labeled as gene rather than a
  laboratory marker/protein marker in the tested Ataxia-telangiectasia
  sentence.

Verdict:

Best local first-pass span annotator for GeneReviews. Use it offline, then
normalize with deterministic dictionaries/linkers.

### Best Variant Specialist: tmVar-PubMedBERT

Model:

`Brizape/tmvar-PubMedBert-finetuned-24-02`

Cache size: `~832M`

Load: `1.54s`

Per-sentence inference: first sentence `0.075s`, then `~0.002-0.003s`.

Strong hits:

- `C282Y`, `p.Cys282Tyr`, `c.845G>A`, `rs1800562`.
- `p.Phe508del`, `F508del`.

Weakness:

- `c.5266dupC` was only partially detected as `c. 5266du`. This needs either
  custom HGVS regex backfill or a better variant post-processor.
- It does not detect genes/diseases/drugs.
- It detects variant spans only; it does not normalize them to rs/CA/HGVS IDs.

Verdict:

Useful as a variant-specialist second pass. Not sufficient by itself.

### Best Normalizing Non-PubTator Candidate: HunFlair2

Model:

`hunflair2` plus `gene-linker`, `disease-linker`, `chemical-linker`

Load/inference:

- NER tagger load: `~1.0-1.3s`.
- NER per sentence after load: `~0.003-0.115s`.
- First linker preprocessing was slow: gene `82.8s`, disease `27.7s`,
  chemical `67.2s`.
- Subsequent linker loads were better: gene `9.8s`, disease `2.1s`,
  chemical `8.4s`.

Strong NER hits:

- `HFE`, `GRIN2B-related`, `CFTR`, `BRCA1` as genes.
- `elexacaftor`, `tezacaftor`, `ivacaftor` as chemicals.
- `hereditary hemochromatosis`, `neurodevelopmental disorder`,
  `developmental delay`, `epilepsy`, `cystic fibrosis`, and HBOC as diseases.

Strong linker hits:

- `GRIN2B-related` -> `2904`
- `CFTR` -> `1080`
- `elexacaftor` -> `MESH:C000629074`
- `tezacaftor` -> `MESH:C000625213`
- `ivacaftor` -> `MESH:C545203`
- `cystic fibrosis` -> `MESH:D003550`

Weaknesses:

- No variant tagging in these examples.
- Some disease spans are too broad, e.g. `breast, thyroid, endometrial, renal,
  and colon cancer`.
- Requires linker cache/preprocessing and warns that `pyab3p` is missing, which
  may reduce abbreviation handling quality.
- The `species-linker` initialization did not complete in a useful time window
  during the broader concept probe, although the NER tagger itself detected
  `Mus musculus` as species. Disease and chemical linkers completed and
  returned useful IDs.

Broader concept pass:

- HunFlair2 was very strong for disease/phenotype-like spans and chemicals:
  `Wilson disease`, `copper`, `penicillamine`, `trientine`, `zinc`,
  `Mitochondrial DNA depletion syndrome`, `hypotonia`, `lactic acidosis`,
  `seizures`, `liver failure`, `cystic fibrosis`, the CFTR modulators,
  `dornase alfa`, `azithromycin`, `Fragile X syndrome`,
  `intellectual disability`, `autism spectrum disorder`, `macroorchidism`,
  `behavioral abnormalities`, `Ataxia-telangiectasia`, `cerebellar ataxia`,
  `telangiectasias`, `immunodeficiency`, `alpha-fetoprotein`, and
  `cancer predisposition`.
- HunFlair2 also handled `A549` as `CellLine` and `Mus musculus` as `Species`.
- It does not distinguish phenotype vs disease; many phenotypes come back as
  `Disease`. That is acceptable for retrieval if we store a broad
  disease_or_phenotype type, but not if we need ontology-clean phenotype
  classification.

Verdict:

HunFlair2 is the strongest practical alternative when normalized IDs are needed.
It should be tested as `HunFlair2 + tmVar-PubMedBERT/HGVS regex`, not as a
standalone replacement.

### GLiNER-BioMed Base

Model:

`Ihor/gliner-biomed-base-v1.0`

Cache size: `~756M`

Load: `1.62s`

Per-sentence inference: `0.006-0.133s`.

Strengths:

- Broad, usable span detection.
- Good on GRIN2B, CFTR, BRCA1, PTEN, diseases, drug combination.

Weaknesses:

- More label-sensitive than the specialized GLiNER model.
- Merged `HFE C282Y` as gene in one case.
- Produced noisy disease labels for anatomical/cancer-screening words.

Verdict:

Good fallback and fast experiment model, but the specialized GLiNER
uni-encoder is cleaner for our entity set.

### OpenMed NER

Tested:

- `OpenMed/OpenMed-ZeroShot-NER-Genomic-Tiny-60M`
- `OpenMed/OpenMed-NER-DiseaseDetect-BioMed-335M`
- `OpenMed/OpenMed-NER-PharmaDetect-BigMed-278M`

Results:

- Genomic tiny failed in standard Transformers with missing `model_type` in
  `config.json`.
- Disease model worked, but only for disease spans.
- Pharma model was not useful on these sentences; it missed the CFTR modulator
  drugs and emitted noisy character-level chemical spans on the PTEN sentence.

Verdict:

Not a good fit for C-gamma.

### scispaCy

Model:

`en_core_sci_sm` via scispaCy `0.5.4`

Load: `0.23s`

Per-sentence CPU inference: `~0.003-0.005s`.

Strengths:

- Very fast broad biomedical term spotting.
- Found many useful spans (`HFE`, `c.845G>A`, `rs1800562`, `GRIN2B-related`,
  `CFTR`, drug combination, diseases).

Weaknesses:

- All labels are generic `ENTITY`; no PubTator-style type separation.
- Missed some exact variant spans, e.g. `C282Y`, `p.Cys282Tyr`, and
  `c.5266dupC`.
- UMLS linking would require a separate linker/semantic-type filtering step.

Verdict:

Useful as a fast broad recall baseline, not as the main C-gamma annotator.

## Recommended Stack To Test Next

Do not chase a monolithic PubTator replacement. The best practical local stack
from this bake-off is a hybrid:

1. **Primary spans:** specialized GLiNER-BioMed uni-encoder
   (`anthonyyazdaniml/gliner-biomed-large-v1.0-disease-chemical-gene-variant-species-cellline-ner`)
2. **Variant backstop:** `Brizape/tmvar-PubMedBert-finetuned-24-02` plus HGVS
   regex repair for partial spans like `c.5266dupC`
3. **Normalization:** HunFlair2 linkers for gene/disease/chemical IDs where
   they are reliable, plus deterministic local gazetteers:
   - HGNC/NCBI Gene for human genes
   - MeSH/HPO for disease/phenotype
   - MeSH/RxNorm-like drug aliases for modulators
   - dbSNP/ClinGen/LitVar-derived variant aliases for common GeneReviews
     variants
   - Species and cell-line IDs only if they prove useful for ranking; they are
     lower priority for GeneReviews retrieval than gene, variant, disease/
     phenotype, and chemical/drug anchors.
4. **Runtime:** no models. Commit/ship only compact `passage_entities`,
   `chapter_entities`, and a query gazetteer.

## Concrete Next Step

Create a probe harness that runs this hybrid on:

- all 299 locked ranking bench queries
- gold passages for the three marquee misses
- 50 random GeneReviews passages from the local corpus bundle

Output one JSONL row per text with:

- model/tool versions
- entity spans
- normalized IDs
- latency
- whether the C-gamma anchor was recovered

Only after that should we design the `passage_entities` schema and retrieval
boosts.

## 2026-05-23 Repo Status Update

Senior MCP/LLM engineering status check against current `main`
(`25744d5`, after group-A API reliability work) confirms that this bake-off has
not been converted into code yet. This review and the paired local annotation
probe are currently local/untracked review artifacts in the working tree.

Current implementation status:

| Area | Status | Notes |
| --- | --- | --- |
| Hybrid GLiNER/tmVar/HunFlair probe | Missing | No script/package/test runs the recommended stack or emits JSONL evidence. |
| GLiNER/tmVar/HunFlair dependencies | Missing except generic Transformers | `transformers` and `sentence-transformers` exist; `gliner`, `flair`/HunFlair2, scispaCy, and gazetteer libraries are not declared. |
| Query gazetteer | Missing | No `genereview_link/entities/` package or deterministic normalized entity extraction from query text. |
| `passage_entities` / `chapter_entities` | Missing | Current migrations cover chapters, passages, embeddings, tables, and `passage_role` only. |
| Entity overlap scoring | Missing | RRF has lexical/dense/role/section-intent scoring, but no entity join or `entity_overlap_boost`. |
| Property-intent table boost | Missing | `table_body` exists but is neutral at multiplier `1.0`; no `indication`/`frequency`/`dose` table preference. |
| PubTator-Link runtime dependency | Avoided | The repo does not import PubTator-Link at runtime, matching the recommendation here. |
| Benchmark fixture | Present | The locked fixture includes HFE, CFTR, and GRIN2B cases. Saved C-alpha RRF results show P@1 `0.408`, Recall@5 `0.826`, no RRF regressions, and one must-change improvement. |

Important correction to the previous "Concrete Next Step":

- The harness still comes first for choosing annotator behavior and weights.
- The minimal entity schema and provenance contract do **not** need to wait for
  model selection. The `../mdr-mcp` comparison shows that a stable
  canonical-entity plus passage-match schema can be designed independently while
  annotators remain swappable.

## Current Repo Interpretation

The current codebase is best described as C-alpha plus API reliability work:

- Search is parallel lexical + dense retrieval with RRF, section priority, and
  role-aware adjusted scoring.
- Tables are first-class passages with `passage_type`, `table_data`, and
  `passage_role`.
- Query guidance and response ergonomics have improved, but biomedical entity
  normalization has not landed.
- The May 13 C-gamma design spec is now partly stale because it assumes a
  PubTator-3-first offline stack. This bake-off and the paired local probe
  should supersede that operational assumption.

The C-gamma problem statement remains valid:

- HFE needs variant alias normalization (`C282Y`, `p.Cys282Tyr`, `c.845G>A`,
  `rs1800562`).
- CFTR needs a table-aware treatment/indication signal and drug/variant anchors.
- GRIN2B needs query-time gene detection and chapter anchoring, not just
  bag-of-words retrieval.

## Lessons From `../mdr-mcp`

Portable ideas:

1. **Entity storage contract.** MDR stores canonical entities separately from
   passage-level matches, with offsets, confidence, source, and source version.
   GeneReview-Link should adapt this as:
   - `biomedical_entities(canonical_id, entity_type, preferred_label, aliases,
     source, source_version, metadata)`
   - `passage_entities(passage_id, canonical_id, entity_type, surface_form,
     start_offset, end_offset, confidence, source, source_version, metadata)`
   - `chapter_entities` as a derived rollup for cheap chapter-level anchors.

2. **Gazetteer implementation shape.** MDR's seed-driven gazetteer uses
   Aho-Corasick when available, regex fallback when not, clean-boundary checks,
   and longest-match dedupe. Biomedical equivalents should draw from HGNC/NCBI
   Gene, MeSH, HPO, RxNorm-like drug aliases, and curated dbSNP/LitVar/ClinGen
   variant aliases.

3. **Offline preparation, read-only serving.** MDR treats extraction,
   annotation, embedding, and DB load as offline preparation; serving only reads
   the prepared corpus. This matches the C-gamma constraint that no biomedical
   NER model runs on the VPS hot path.

4. **Replace-by-source/version ingestion.** MDR's ingestion service can replace
   annotations by provenance. GeneReview-Link should use the same operational
   idea for repeatable annotator reruns.

5. **Parent/leaf collapse as later retrieval ergonomics.** MDR searches leaf
   passages but can display parent passages. For GeneReviews, a similar
   section/table grouping may help after entity overlap is in place, especially
   for CFTR table-vs-narrative ordering. It is not required for the first entity
   milestone.

Not portable:

- MDR's regulatory labels, actor/artifact vocabulary, clause parser, and
  standards-citation logic. GeneReview-Link needs biomedical IDs and
  NBK/chapter/section/table context instead.

## Revised Implementation Direction

Use this bake-off to supersede the PubTator-3-first C-gamma plan:

1. **Probe first for evidence, not production coupling.**
   Build `scripts/probe_biomedical_annotation.py` as an offline script. Keep
   heavy optional packages out of core dependencies until a stack wins.

2. **Investigate deterministic query gazetteer sources early.**
   Do not implement a hand-curated seed set around the known C-gamma examples.
   Treat HFE/C282Y, CFTR/F508del/modulators, GRIN2B, cystic fibrosis, and
   hereditary hemochromatosis as benchmark probes and regression tests. The
   implementation data should come from authoritative sources such as HGNC/NCBI
   Gene, MeSH, HPO, RxNorm, NCBI Variation Services, ClinGen Allele Registry,
   LitVar/tmVar outputs, and GeneReviews-derived alias mining.

3. **Design stable storage independent of annotator choice.**
   Use the MDR-style canonical entity + passage match schema. Do not encode
   GLiNER, tmVar, HunFlair, or PubTator-specific assumptions into table shape.

4. **Wire scoring after query and passage entities both exist.**
   Add `entity_overlap_boost` only after query gazetteer output and at least a
   small passage-entity fixture can be tested. Expose the boost in
   `ScoreBreakdown`.

5. **Add the missing table-property intent.**
   Extend query intent detection with `indication`, `frequency`, `dose`,
   `onset`, `penetrance`, and `carrier`; apply a small `table_body` preference
   only when those property intents are present.

6. **Gate with the existing bench.**
   Rerun `make bench-ranking` once entity overlap is wired. Required gate should
   remain: Recall@5 at least `0.826`, P@1 materially above `0.408`, no RRF
   exact-symbol regressions, and at least two of the three C-gamma anchor cases
   fixed.
