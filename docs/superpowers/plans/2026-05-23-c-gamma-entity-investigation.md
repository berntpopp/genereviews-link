# C-gamma Entity Investigation Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Systematically investigate production biomedical entity sources, annotation candidates, and benchmark impact before deciding which C-gamma entity-aware retrieval implementation to build.

**Architecture:** This branch is a research and decision branch, not a production entity implementation branch. It produces source inventories, small reproducible probes, benchmark diagnostics, and a final decision memo. HFE, CFTR, and GRIN2B are treated as benchmark probes and regression tests, not as hand-curated implementation data.

**Tech Stack:** Python 3.12, `uv`, FastAPI/MCP codebase context, existing ranking bench, PostgreSQL corpus where available, official biomedical sources (HGNC/NCBI Gene, MeSH, HPO, RxNorm, NCBI Variation Services, ClinGen Allele Registry, LitVar/tmVar), optional offline model probes with GLiNER, tmVar-PubMedBERT, and HunFlair2.

---

## Starting Point

- Branch/worktree: `feat/c-gamma-entity-investigation` at `.worktrees/c-gamma-entity-investigation`.
- Baseline setup command: `make install`.
- Baseline unit command: `make test-unit`.
- Baseline result observed at branch creation: `576 passed in 15.41s`.
- Source reviews:
  - `docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md`
  - `docs/superpowers/reviews/2026-05-14-pubtator-style-alternatives-rtx-bakeoff.md`
- Existing C-gamma spec to supersede operationally:
  - `docs/superpowers/specs/2026-05-13-ranking-redesign-phase-c-gamma-entity-aware-design.md`

## Decision Principles

- Do not implement a hardcoded HFE/CFTR/GRIN2B gazetteer as production data.
- Use HFE/CFTR/GRIN2B only as benchmark probes for source coverage, query extraction, passage annotation, and ranking impact.
- Prefer official, versioned, reproducible bulk sources over live request-time APIs.
- Keep all biomedical model inference offline. Runtime search must use prepared artifacts and deterministic lookup only.
- Keep source provenance first-class: every alias or match needs source and source version.
- Produce evidence before choosing schema, dependencies, and ranking weights.

## Files To Create Or Modify

- Modify: `docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md`
  - Correct the next steps so HFE/CFTR/GRIN2B are probes, not implementation seed data.
- Modify: `docs/superpowers/reviews/2026-05-14-pubtator-style-alternatives-rtx-bakeoff.md`
  - Correct the deterministic gazetteer step to require authoritative source investigation first.
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-source-inventory.md`
  - Official source inventory and suitability assessment.
- Create: `docs/superpowers/specs/2026-05-23-c-gamma-entity-source-design.md`
  - Proposed production source pipeline, storage contract, provenance model, and refresh cadence.
- Create: `scripts/research/probe_entity_sources.py`
  - Research-only probe CLI for official source/API smoke checks and local artifact parsing.
- Create: `scripts/research/probe_biomedical_annotators.py`
  - Research-only optional model probe wrapper for GLiNER/tmVar/HunFlair candidates.
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-source-probe-results.md`
  - Results from official-source probes.
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-annotator-probe-results.md`
  - Results from offline model probes.
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-benchmark-diagnosis.md`
  - Current ranking failure analysis and expected entity features per bench case.
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-implementation-decision.md`
  - Final recommendation: what to implement first, why, gates, and rejected options.

## Task 1: Correct The Review Docs On This Branch

**Files:**
- Modify: `docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md`
- Modify: `docs/superpowers/reviews/2026-05-14-pubtator-style-alternatives-rtx-bakeoff.md`

- [ ] **Step 1: Remove hardcoded-seed-first wording**

  In both review docs, ensure the revised next steps say that known C-gamma anchors are benchmark probes, not production seed data.

- [ ] **Step 2: Verify the correction text**

  Run:

  ```bash
  rg -n "hand-curated|benchmark probes|authoritative sources|seed set" docs/superpowers/reviews/2026-05-14-*.md
  ```

  Expected: both May 14 docs explicitly warn against using a hand-curated HFE/CFTR/GRIN2B seed set as implementation data.

- [ ] **Step 3: Commit**

  ```bash
  git add docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md docs/superpowers/reviews/2026-05-14-pubtator-style-alternatives-rtx-bakeoff.md
  git commit -m "docs: clarify entity investigation direction"
  ```

## Task 2: Capture Baseline Benchmark And Failure Context

**Files:**
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-benchmark-diagnosis.md`
- Read: `tests/fixtures/ranking_bench.jsonl`
- Read: `bench_ranking_results.json`
- Read: `docs/superpowers/specs/2026-05-13-ranking-redesign-phase-c-gamma-entity-aware-design.md`
- Read: `docs/superpowers/reviews/2026-05-12-c-alpha-bench-results.md`

- [ ] **Step 1: Extract the current C-gamma target rows**

  Run:

  ```bash
  nl -ba tests/fixtures/ranking_bench.jsonl | sed -n '296,300p'
  ```

  Expected: HFE, CFTR, GRIN2B, Lynch, and BRCA1 target rows are visible.

- [ ] **Step 2: Capture saved benchmark metrics**

  Run:

  ```bash
  jq '.rrf' bench_ranking_results.json
  ```

  Expected: RRF P@1, MRR@5, Recall@5, regressions, and improvements are visible.

- [ ] **Step 3: Write the diagnosis doc**

  Create `docs/superpowers/reviews/2026-05-23-c-gamma-benchmark-diagnosis.md` with these sections:

  ```markdown
  # C-gamma Benchmark Diagnosis

  **Date:** 2026-05-23
  **Branch:** `feat/c-gamma-entity-investigation`

  ## Current Baseline

  - RRF P@1: 0.4080267558528428
  - RRF Recall@5: 0.8260869565217391
  - RRF exact-symbol regressions: 0
  - RRF must-change improvements: 1 (`BRCA1 risk-reducing mastectomy salpingo-oophorectomy`)

  ## Target Rows

  | Query | Expected | Current issue | Entity evidence needed |
  | --- | --- | --- | --- |
  | HFE C282Y allele frequency | NBK1440:0051 | Variant aliases need normalization | HFE gene, C282Y/p.Cys282Tyr/c.845G>A/rs1800562 equivalence |
  | CFTR F508del CFTR modulator therapy indication | NBK1250:0032 | Table vs adjacent narrative ordering | CFTR gene, F508del/p.Phe508del equivalence, modulator drug entities, table property intent |
  | GRIN2B-related neurodevelopmental disorder phenotype spectrum | NBK501979:0005 | Query gene is not structural anchor | GRIN2B gene normalization and chapter gene anchor |

  ## Investigation Questions

  - Which official sources cover each needed entity class?
  - Which sources provide aliases vs normalized identifiers?
  - Which sources are bulk-downloadable and versionable?
  - Which sources require online lookup and must remain offline-build only?
  - Which entity feature can plausibly improve ranking without lowering Recall@5?
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add docs/superpowers/reviews/2026-05-23-c-gamma-benchmark-diagnosis.md
  git commit -m "docs: diagnose c-gamma benchmark targets"
  ```

## Task 3: Inventory Authoritative Entity Sources

**Files:**
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-source-inventory.md`

**Primary sources to inspect:**
- HGNC REST docs: `https://www.genenames.org/help/rest/`
- NCBI Gene alias guidance: `https://support.nlm.nih.gov/knowledgebase/article/KA-03556/en-us`
- MeSH downloads: `https://www.nlm.nih.gov/databases/download/mesh.html`
- MeSH RDF: `https://id.nlm.nih.gov/mesh/`
- HPO downloads: `https://human-phenotype-ontology.github.io/downloads.html`
- RxNorm Prescribable API: `https://lhncbc.nlm.nih.gov/RxNav/APIs/PrescribableAPIs.html`
- NCBI Variation Services/SPDI: `https://api.ncbi.nlm.nih.gov/variation/v0`
- ClinGen Allele Registry API docs: `https://reg.clinicalgenome.org/doc/AlleleRegistry_1.01.xx_api_v1.pdf`
- LitVar help: `https://www.ncbi.nlm.nih.gov/CBBresearch/Lu/Demo/LitVar/help.html`

- [ ] **Step 1: Write source table**

  Create an inventory table with this exact shape:

  ```markdown
  | Entity class | Candidate source | Access mode | Bulk/versionable | Useful fields | Known gaps | Runtime allowed? |
  | --- | --- | --- | --- | --- | --- | --- |
  | Gene | HGNC | REST/custom download | yes | approved symbol, alias symbol, previous symbol, HGNC ID, NCBI Gene ID | non-human excluded by design | no, build-time only |
  | Gene | NCBI Gene gene_info | FTP bulk | yes | GeneID, Symbol, Synonyms, description, tax_id | synonym noise | no, build-time only |
  | Disease/chemical | MeSH | XML/RDF/API | yes | descriptor ID, label, entry terms, SCRs | phenotype granularity weaker than HPO | no, build-time only |
  | Phenotype | HPO | OBO/OWL/JSON | yes | HP ID, label, synonyms | disease names are not primary scope | no, build-time only |
  | Drug | RxNorm | API/download/API display terms | partly | RxCUI, name, synonym, term type | CFTR modulators may need MeSH/SCR or manual validation | no, build-time only |
  | Variant | NCBI Variation Services/SPDI | API | partial | SPDI, HGVS, rs, canonical allele | protein shorthand needs mapping context | no, build-time only |
  | Variant | ClinGen Allele Registry | API | partial | CA ID, HGVS, external IDs | API/bulk availability must be verified | no, build-time only |
  | Variant | LitVar/tmVar | API/tool output | partial | variant mentions, dbSNP/LitVar IDs | not a clean authoritative alias dump | no, build-time/offline only |
  | GeneReviews local | Corpus table mining | local corpus | yes | HGVS strings, protein aliases, table context | needs normalization from external source | yes, prepared artifact only |
  ```

- [ ] **Step 2: Add acceptance criteria**

  Add a section requiring a source to be accepted only if it provides:

  ```markdown
  - stable identifier
  - alias/surface form
  - source name
  - source version or retrieval date
  - reproducible build path
  - clear runtime policy
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add docs/superpowers/reviews/2026-05-23-c-gamma-source-inventory.md
  git commit -m "docs: inventory biomedical entity sources"
  ```

## Task 4: Probe Official Source Coverage

**Files:**
- Create: `scripts/research/probe_entity_sources.py`
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-source-probe-results.md`

- [ ] **Step 1: Create research script directory**

  Run:

  ```bash
  mkdir -p scripts/research
  ```

- [ ] **Step 2: Add probe CLI**

  Create `scripts/research/probe_entity_sources.py` with this interface:

  ```python
  """Research-only probe for C-gamma biomedical entity source coverage."""

  from __future__ import annotations

  import argparse
  import json
  from dataclasses import asdict, dataclass
  from pathlib import Path


  @dataclass(frozen=True, slots=True)
  class ProbeCase:
      entity_class: str
      query: str
      expected_hint: str


  DEFAULT_CASES = (
      ProbeCase("gene", "GRIN2B", "NCBI Gene 2904"),
      ProbeCase("gene", "HFE", "NCBI Gene 3077"),
      ProbeCase("variant", "C282Y", "rs1800562 or equivalent allele"),
      ProbeCase("variant", "p.Cys282Tyr", "rs1800562 or equivalent allele"),
      ProbeCase("variant", "c.845G>A", "rs1800562 or equivalent allele"),
      ProbeCase("gene", "CFTR", "NCBI Gene 1080"),
      ProbeCase("variant", "F508del", "rs113993960 or equivalent allele"),
      ProbeCase("variant", "p.Phe508del", "rs113993960 or equivalent allele"),
      ProbeCase("drug", "elexacaftor", "RxNorm/MeSH concept"),
      ProbeCase("drug", "tezacaftor", "RxNorm/MeSH concept"),
      ProbeCase("drug", "ivacaftor", "RxNorm/MeSH concept"),
      ProbeCase("disease", "cystic fibrosis", "MeSH D003550 or equivalent"),
      ProbeCase("disease", "hereditary hemochromatosis", "MeSH/OMIM equivalent"),
  )


  def main() -> None:
      parser = argparse.ArgumentParser()
      parser.add_argument("--out", type=Path, required=True)
      args = parser.parse_args()
      rows = [
          {
              **asdict(case),
              "status": "not_checked",
              "source_results": [],
              "notes": "Source-specific probes are added in later research commits after access paths are selected.",
          }
          for case in DEFAULT_CASES
      ]
      args.out.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")


  if __name__ == "__main__":
      main()
  ```

  This initial script intentionally records the probe case set and output schema. Add source-specific calls only after the source inventory identifies the right access path.

- [ ] **Step 3: Run the schema-only probe**

  Run:

  ```bash
  uv run python scripts/research/probe_entity_sources.py --out /tmp/genereviews-c-gamma-source-probe.jsonl
  head -n 3 /tmp/genereviews-c-gamma-source-probe.jsonl
  ```

  Expected: JSONL rows with `status: "not_checked"` and the C-gamma probe cases.

- [ ] **Step 4: Write source probe results doc**

  Create `docs/superpowers/reviews/2026-05-23-c-gamma-source-probe-results.md` with:

  ```markdown
  # C-gamma Source Probe Results

  **Date:** 2026-05-23
  **Branch:** `feat/c-gamma-entity-investigation`

  ## Probe Contract

  The source probe treats known C-gamma anchors as benchmark probes, not implementation seed data.

  ## Current Status

  The first checked-in script defines the probe cases and JSONL schema. Source-specific probes are added only after the source inventory selects access paths.

  ## Required Output Fields

  - entity_class
  - query
  - expected_hint
  - status
  - source_results
  - notes
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/research/probe_entity_sources.py docs/superpowers/reviews/2026-05-23-c-gamma-source-probe-results.md
  git commit -m "research: scaffold entity source coverage probe"
  ```

## Task 5: Design The Production Entity Source Contract

**Files:**
- Create: `docs/superpowers/specs/2026-05-23-c-gamma-entity-source-design.md`
- Read: `/home/bernt-popp/development/mdr-mcp/mdr_mcp/db/schema.py`
- Read: `/home/bernt-popp/development/mdr-mcp/mdr_mcp/gazetteer/regulatory.py`

- [ ] **Step 1: Write the design doc**

  Create the design doc with these sections:

  ```markdown
  # C-gamma Entity Source Design

  **Date:** 2026-05-23
  **Branch:** `feat/c-gamma-entity-investigation`

  ## Goal

  Define a production entity source and storage contract for biomedical entity-aware retrieval without choosing the final annotator prematurely.

  ## Non-goals

  - No runtime calls to external biomedical APIs.
  - No hardcoded HFE/CFTR/GRIN2B seed gazetteer as production data.
  - No PubTator-3 full stack as default implementation path until operational cost is justified.

  ## Proposed Tables

  ### biomedical_entities

  - canonical_id text primary key
  - entity_type text not null
  - preferred_label text not null
  - aliases jsonb not null
  - source text not null
  - source_version text not null
  - metadata jsonb not null default '{}'

  ### passage_entities

  - id bigint identity primary key
  - nbk_id text not null
  - passage_id text not null
  - canonical_id text not null
  - entity_type text not null
  - surface_form text not null
  - start_offset integer not null
  - end_offset integer not null
  - confidence double precision not null
  - source text not null
  - source_version text not null
  - metadata jsonb not null default '{}'

  ### chapter_entities

  Derived rollup by nbk_id, entity_type, canonical_id, source_version.

  ## Build Flow

  1. Fetch/version official source artifacts offline.
  2. Normalize source aliases into canonical entity rows.
  3. Run passage annotators offline against corpus text.
  4. Load passage matches with provenance.
  5. Build deterministic query matcher from the same alias artifact.
  6. Measure benchmark impact before enabling ranking boost.

  ## Decision Gates

  - Source coverage for benchmark probes documented.
  - Query matcher p95 target under 20 ms on VPS-class hardware.
  - Passage annotation artifact can be rebuilt from pinned source versions.
  - RRF Recall@5 does not regress below 0.826.
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add docs/superpowers/specs/2026-05-23-c-gamma-entity-source-design.md
  git commit -m "docs: design c-gamma entity source contract"
  ```

## Task 6: Plan And Run Offline Annotator Probes

**Files:**
- Create: `scripts/research/probe_biomedical_annotators.py`
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-annotator-probe-results.md`

- [ ] **Step 1: Write the probe wrapper contract**

  The script must support these modes:

  ```bash
  uv run python scripts/research/probe_biomedical_annotators.py --mode write-inputs --out /tmp/genereviews-c-gamma-annotator-inputs.jsonl
  uv run --with gliner python scripts/research/probe_biomedical_annotators.py --mode gliner --input /tmp/genereviews-c-gamma-annotator-inputs.jsonl --out /tmp/genereviews-c-gamma-gliner.jsonl
  uv run python scripts/research/probe_biomedical_annotators.py --mode tmvar-pubmedbert --input /tmp/genereviews-c-gamma-annotator-inputs.jsonl --out /tmp/genereviews-c-gamma-tmvar.jsonl
  ```

  The wrapper must not add heavy model packages to `pyproject.toml` during investigation.

- [ ] **Step 2: Define output schema**

  Each JSONL row must include:

  ```json
  {
    "text_id": "bench:HFE",
    "text": "HFE C282Y allele frequency",
    "tool": "gliner",
    "tool_version": "package version or unknown",
    "latency_ms": 0.0,
    "spans": [
      {
        "start": 0,
        "end": 3,
        "surface": "HFE",
        "label": "gene",
        "normalized_id": null,
        "confidence": 0.0
      }
    ]
  }
  ```

- [ ] **Step 3: Run only available probes**

  Run the probes that can execute locally without large new downloads. If a model is unavailable, record that in the results doc instead of forcing a dependency into the runtime project.

- [ ] **Step 4: Commit probe wrapper and results**

  ```bash
  git add scripts/research/probe_biomedical_annotators.py docs/superpowers/reviews/2026-05-23-c-gamma-annotator-probe-results.md
  git commit -m "research: probe biomedical annotator candidates"
  ```

## Task 7: Produce Implementation Decision Memo

**Files:**
- Create: `docs/superpowers/reviews/2026-05-23-c-gamma-implementation-decision.md`
- Read all C-gamma docs created in Tasks 2-6.

- [ ] **Step 1: Write decision matrix**

  Include this matrix:

  ```markdown
  | Option | Coverage | Operational cost | Runtime risk | Bench impact confidence | Decision |
  | --- | --- | --- | --- | --- | --- |
  | Deterministic official-source query matcher first | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | ship/defer/reject plus reason |
  | Passage entity schema + loader first | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | ship/defer/reject plus reason |
  | GLiNER/tmVar/HunFlair offline annotation first | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | ship/defer/reject plus reason |
  | PubTator-3 full stack first | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | ship/defer/reject plus reason |
  | Reranker-only improvements | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | High/Medium/Low plus one-sentence evidence | ship/defer/reject plus reason |
  ```

- [ ] **Step 2: State first implementation recommendation**

  The recommendation must name:

  - first production milestone
  - files to create
  - dependencies to add or avoid
  - benchmark gate
  - known risks
  - follow-up milestone

- [ ] **Step 3: Commit**

  ```bash
  git add docs/superpowers/reviews/2026-05-23-c-gamma-implementation-decision.md
  git commit -m "docs: decide c-gamma entity implementation path"
  ```

## Task 8: Final Branch Verification

**Files:**
- No new files unless a verification note is needed.

- [ ] **Step 1: Run format/lint for Python research scripts**

  ```bash
  uv run ruff format scripts/research
  uv run ruff check scripts/research
  ```

  Expected: no Ruff errors.

- [ ] **Step 2: Run focused tests**

  ```bash
  make test-unit
  ```

  Expected: all unit tests pass.

- [ ] **Step 3: Summarize branch state**

  Run:

  ```bash
  git log --oneline --decorate -n 10
  git status --short --branch
  ```

  Expected: branch contains the investigation commits and no unexpected tracked changes. Pre-existing untracked files should be called out without deleting them.

## Completion Criteria

This branch is ready for implementation planning only when it has:

- a source inventory grounded in official biomedical sources;
- a benchmark diagnosis that explains what entity evidence each failure needs;
- a source probe contract and initial results;
- an annotator probe contract and available local results;
- a production entity source/storage design;
- an implementation decision memo with a recommended first build milestone;
- passing unit baseline after research scripts are added.
