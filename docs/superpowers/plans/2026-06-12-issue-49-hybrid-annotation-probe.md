# Issue #49 — Hybrid Local Biomedical Annotation Probe

**Date:** 2026-06-12
**Issue:** #49 — Evaluate hybrid local biomedical entity annotation for C-gamma retrieval
**Type:** SPIKE — deliverable is a script + summary report, not a production feature
**Prior art:**
- `docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md`
- `docs/superpowers/reviews/2026-05-14-pubtator-style-alternatives-rtx-bakeoff.md`
**Effort:** M (2–4 focused sessions)

---

## 1. Summary and Goal

Build a bounded, reproducible offline probe that runs the recommended hybrid
annotation pipeline over three corpus slices and measures whether the three
C-gamma marquee misses are recoverable via entity-level span detection:

- **HFE C282Y** — variant-nomenclature asymmetry (`C282Y` / `p.Cys282Tyr` /
  `c.845G>A` / `rs1800562`); gold passage `NBK1440:0051`.
- **CFTR F508del modulator therapy** — drug/variant spans in a terse table
  body; gold passages `NBK1250:0032`, `NBK1250:0057`.
- **GRIN2B-related neurodevelopmental disorder** — gene symbol treated as
  bag-of-words; gold passages `NBK501979:0005`, `NBK501979:0009`,
  `NBK501979:0016`.

### Non-goals (explicit)

- No production runtime model dependency introduced (no `gliner`, `flair`, or
  `torch`-GPU requirement in the core install).
- No database schema changes (`passage_entities`, `chapter_entities` tables)
  until probe demonstrates useful coverage.
- No retrieval boost (`entity_overlap_boost`) wired into RRF until entity
  schema and schema are chosen.
- No fine-tuning or training of any model.
- No GPU requirement — probe must run CPU-only on the dev workstation.

---

## 2. Probe Harness Design

### 2.1 Script location

`scripts/probe_hybrid_annotation.py`

Run with:

```
uv run --with "gliner>=0.4,<1" \
       --with "flair>=0.15,<1" \
       --with "torch>=2.2" \
       python scripts/probe_hybrid_annotation.py [OPTIONS]
```

The `--with` flags keep heavy deps out of the core install; no `pyproject.toml`
production dependency change is needed for the probe itself (see §4 for the
optional extras group).

### 2.2 Inputs

The script accepts three corpus slices, all resolved locally without a running
server:

| Slice | Source | How loaded |
|---|---|---|
| 299 ranking-bench queries | `tests/fixtures/ranking_bench.jsonl` | `json.loads` line-by-line; fields `query`, `chapter_nbk_id`, `expected_top1_passage_id` |
| 3 C-gamma gold passages (text) | DB query via `asyncpg` or from cached labeled passages `tests/fixtures/labeled_passages.jsonl` | fallback: construct from NXML under `tests/fixtures/nxml/` |
| 50 random corpus passages | Sample from `tests/fixtures/labeled_passages.jsonl`; seed=42 for reproducibility | `random.seed(42); random.sample(rows, 50)` |

CLI flags:

```
--bench       PATH   default: tests/fixtures/ranking_bench.jsonl
--labeled     PATH   default: tests/fixtures/labeled_passages.jsonl
--out-dir     PATH   default: /tmp/probe_hybrid_annotation  (git-ignored)
--seed        INT    default: 42
--device      STR    default: cpu
--threshold   FLOAT  default: 0.4   (GLiNER confidence cutoff)
--no-hunflair         skip HunFlair2 linkers (faster)
```

### 2.3 Hybrid pipeline wiring

One pipeline object loads all models once and runs them in sequence per text.

| Entity category | Primary model | Secondary / backfill | Normalization |
|---|---|---|---|
| Gene | HunFlair2 NER + gene-linker | GLiNER fallback if HunFlair2 span missing | NCBI Gene ID from linker; HGNC symbol from span surface |
| Variant | GLiNER (label `sequence variant`) + tmVar-PubMedBERT | HGVS regex backfill (see §2.4) | Surface form; rsID if regex extracts it |
| Disease | HunFlair2 disease-linker | GLiNER (label `disease`) | MeSH ID from linker |
| Phenotype/symptom | GLiNER (labels `phenotype`, `symptom`) | — | HPO match if surface in local seed dict |
| Chemical/drug | HunFlair2 chemical-linker | GLiNER (label `drug`, `chemical`) | MeSH ID from linker |

GLiNER model: `anthonyyazdaniml/gliner-biomed-large-v1.0-disease-chemical-gene-variant-species-cellline-ner`
(~1.8 GB cached; Apache-2.0; CPU-capable).

tmVar-PubMedBERT model: `Brizape/tmvar-PubMedBert-finetuned-24-02`
(~832 MB cached; Apache-2.0; CPU-capable).

HunFlair2: installed via `flair>=0.15`; models `hunflair2`,
`hunflair/hunflair2-gene-linker`, `hunflair/hunflair2-disease-linker`,
`hunflair/hunflair2-chemical-linker`.
Flair: MIT license; HunFlair2 model cards: Apache-2.0 / MIT.
First-time linker preprocessing ~30–80 s per linker (cached after first run).

Span merge strategy: longest-match wins; ties broken by confidence desc.
Source tag on each span records which model produced it.

### 2.4 HGVS regex backfill

Applied after model spans are merged to catch partial detections (e.g.
tmVar-PubMedBERT mis-tokenizing `c.5266dupC`):

```python
import re
HGVS_PATTERNS = [
    r'\b[cpgmno]\.[A-Za-z0-9_\-\*\+\.>delinsdup\[\]]+',   # HGVS c./p./g.
    r'\b[A-Z][a-z]{2}\d+[A-Z][a-z]{2}\b',                   # p.Cys282Tyr form
    r'\b[A-Z]\d+[A-Z]\b',                                    # C282Y / F508del shorthand
    r'\brs\d{5,}\b',                                         # dbSNP rsID
]
```

For each pattern match not already covered by a model span, add a synthetic
span with `source="hgvs_regex"`, label `variant`, `confidence=1.0`.

### 2.5 Anchor recovery check

For each bench query entry with a `chapter_nbk_id`, define expected anchors
from a hardcoded seed dict (extendable):

```python
CGAMMA_ANCHORS = {
    "NBK1440": {"gene": ["HFE"], "variant": ["C282Y","p.Cys282Tyr","c.845G>A","rs1800562"]},
    "NBK1250": {"gene": ["CFTR"], "variant": ["p.Phe508del","F508del"],
                "drug": ["elexacaftor","tezacaftor","ivacaftor"]},
    "NBK501979": {"gene": ["GRIN2B"]},
}
```

`anchor_recovered: bool` is `True` if at least one expected anchor surface form
(case-insensitive) appears in the merged span list for that text.

---

## 3. Output Schema

One JSONL file per run: `{out_dir}/probe_results_{ISO_DATE}.jsonl`.
One row per annotated text unit.

```jsonc
{
  "text_id":        "NBK1440:0051",          // passage_id or "bench:{n}" or "random:{n}"
  "source_slice":   "bench|gold|random",
  "query_or_text":  "HFE C282Y allele frequency",
  "model_versions": {
    "gliner":       "anthonyyazdaniml/gliner-biomed-large-v1.0-...",
    "tmvar":        "Brizape/tmvar-PubMedBert-finetuned-24-02",
    "hunflair2":    "flair==0.15.x",
    "hgvs_regex":   "v1"
  },
  "spans": [
    {
      "text":        "C282Y",
      "label":       "variant",
      "start":       4,
      "end":         9,
      "confidence":  0.989,
      "source":      "tmvar",
      "norm_id":     null        // populated when linker provides
    }
  ],
  "latency_ms":           {
    "gliner":   24,
    "tmvar":    3,
    "hunflair2":115,
    "total":    142
  },
  "anchor_recovered":     true,
  "expected_anchors":     ["C282Y","p.Cys282Tyr"],   // null when not a bench/gold row
  "chapter_nbk_id":       "NBK1440"
}
```

A companion `{out_dir}/probe_summary_{ISO_DATE}.json` holds aggregate metrics
(see §5). Both paths are git-ignored (see §3.1).

### 3.1 .gitignore entry

Add to `.gitignore` (probe outputs stay outside git):

```
/tmp/probe_hybrid_annotation/
probe_results_*.jsonl
probe_summary_*.json
```

Alternatively write to `/tmp/probe_hybrid_annotation/` which is already outside
the repo.

---

## 4. Dependencies — Optional Extras Group

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
annotation-probe = [
    "gliner>=0.4.1,<1.0",
    "flair>=0.15.1,<1.0",
    # torch is already a transitive dep via transformers/sentence-transformers;
    # CPU-only wheel is sufficient for this probe
    "torch>=2.2,<3.0",
    "ahocorasick-rs>=0.12,<1.0",  # fast gazetteer seed lookup (optional speedup)
    "scispacy>=0.5.4,<1.0",       # optional fast baseline; not required for core
]
```

Install for probe work only:
```
uv sync --extra annotation-probe
```

Or one-shot without touching the lockfile:
```
uv run --with "gliner>=0.4.1" --with "flair>=0.15.1" python scripts/probe_hybrid_annotation.py
```

Core production install is unchanged; `gliner` and `flair` never enter
`dependencies` or `dev` groups.

Model download sizes (CPU-only, from HF cache):
- GLiNER-biomed-large: ~1.8 GB
- tmVar-PubMedBERT: ~832 MB
- HunFlair2 NER + 3 linkers: ~1–3 GB total (linker KBs cached locally)

---

## 5. Evaluation

### 5.1 Per-category recall

For each of the four entity categories (gene, variant, disease/phenotype,
drug/chemical), compute:

```
recall = |recovered anchors| / |total expected anchors|
```

over the 3 C-gamma gold rows and the bench rows that have a `chapter_nbk_id`
in `CGAMMA_ANCHORS`.

Also report per-source contribution: what fraction of recovered spans came from
GLiNER, tmVar, HunFlair2, or HGVS regex.

### 5.2 Latency profile

Report median and p95 `total_latency_ms` across all 349+ rows (299 bench + 3
gold + 50 random). Flag if median > 5 s CPU (threshold for "acceptable for
offline batch").

### 5.3 Summary report shape (`probe_summary_*.json`)

```jsonc
{
  "run_date": "2026-...",
  "device": "cpu",
  "n_texts": 352,
  "model_versions": { ... },
  "recall_by_category": {
    "gene":              {"n_expected": 6, "n_recovered": 5, "recall": 0.833},
    "variant":           {"n_expected": 9, "n_recovered": 7, "recall": 0.778},
    "disease_phenotype": {"n_expected": 3, "n_recovered": 3, "recall": 1.0},
    "drug_chemical":     {"n_expected": 3, "n_recovered": 3, "recall": 1.0}
  },
  "anchor_recovery_cgamma": {
    "NBK1440_HFE":   true,
    "NBK1250_CFTR":  true,
    "NBK501979_GRIN2B": false
  },
  "latency_ms": {"median": 280, "p95": 850},
  "span_source_breakdown": {
    "gliner": 0.52, "tmvar": 0.18, "hunflair2": 0.24, "hgvs_regex": 0.06
  }
}
```

A human-readable text summary is printed to stdout at the end of the run.

---

## 6. Acceptance Criteria Mapping

| Issue criterion | How satisfied |
|---|---|
| Reproducible script under `scripts/` or `docs/superpowers/` | `scripts/probe_hybrid_annotation.py`; seed-fixed random sample; pinned model IDs |
| Probe outputs ignored or outside git | `/tmp/probe_hybrid_annotation/` + `.gitignore` entries |
| Summary report includes recall for gene, variant, disease/phenotype, drug/chemical | `probe_summary_*.json` §5.3 `recall_by_category` |
| No production runtime dependency on model inference | `annotation-probe` optional extras group only; not in `dependencies` |
| No schema/retrieval changes until probe demonstrates coverage | Explicitly deferred; probe output is the gate |

---

## 7. Risks and Effort

| Risk | Severity | Mitigation |
|---|---|---|
| HunFlair2 linker first-run preprocessing slow (~80 s per linker) | Low | One-time; cached in `~/.cache/flair/`. Pre-warm step documented in script `--dry-run` mode. |
| GLiNER bi-encoder model (~1.8 GB) not yet in HF cache | Low | Specify `TRANSFORMERS_CACHE` or `HF_HOME`; auto-downloaded on first run. |
| tmVar-PubMedBERT partial HGVS detection | Low | HGVS regex backfill (§2.4) explicitly handles `c.5266dupC`-class failures. |
| `labeled_passages.jsonl` missing gold text for all three C-gamma passages | Medium | Fallback: fetch passage text via `asyncpg` direct DB query; documented in script. |
| CPU-only inference speed (tmVar ~0.075 s/sentence cold, HunFlair2 ~0.115 s) | Low | 352 texts × ~0.5 s average = ~3 min total; acceptable for a one-shot probe. |
| Model cards/licenses: GLiNER-biomed Apache-2.0; flair MIT; tmVar Apache-2.0 | None | All permissive; compatible with project MIT license. |
| OpenBioNER (2025 NAACL) or newer models worth adding | Low | Note-only: add as a comparison column in the summary if time permits; not a gate. |

**Effort estimate: M** — 2–4 focused sessions.
Breakdown: ~0.5 session for script skeleton + I/O, ~1 session for pipeline
wiring + span merge, ~0.5 session for HGVS regex + anchor check, ~0.5–1 session
for eval aggregation + summary report.

**Deliverable:** `scripts/probe_hybrid_annotation.py` + one
`probe_summary_*.json` run artifact committed as a review comment on issue #49.
No production code changes; no schema migrations; no retrieval changes.

---

## 8. Later (out of scope for this probe)

After the probe shows coverage >= 2/3 C-gamma anchors recovered per category:

1. Design `passage_entities` / `chapter_entities` schema (MDR provenance pattern
   from `docs/superpowers/reviews/2026-05-14-local-annotation-tool-probe.md`
   §Lessons From `../mdr-mcp`).
2. Build deterministic query gazetteer (`genereview_link/entities/gazetteer.py`)
   as the runtime path (no model on hot path).
3. Wire `entity_overlap_boost` into `ScoreBreakdown` and rerun
   `make bench-ranking` with gate: Recall@5 >= 0.826, P@1 materially above
   0.408, all three C-gamma misses resolved.
