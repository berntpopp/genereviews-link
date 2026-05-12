# Chunker data-loss findings and guardrail design

**Date:** 2026-05-12
**Status:** Findings + design for fix.  Implementation tracked in tasks #22-#27.

## What broke

`genereview_link/corpus/nxml.py:217-308` `_walk_section` walks the direct
children of each `<sec>` and only handles `<p>`, `<table-wrap>`, `<sec>`.
Every other tag falls through line 308's "Any other child tags … are
intentionally ignored" branch — silently.

In JATS-NXML, `<list>` is the canonical wrapper for bulleted clinical
recommendations.  GeneReviews authors put management bullets, surveillance
schedules, risk-stratified counseling steps, etc. inside `<list>`.  All of
it has been silently absent from the corpus, embeddings, FTS index, and
every API/MCP response.

## Census (corpus-wide, all 1003 NXMLs in gene_NBK1116.tar.gz, 2026-05-10)

| Tag           | Direct-of-`<sec>` count | Text content (chars) | Status                |
|---------------|------------------------:|---------------------:|-----------------------|
| `p`           |                  83,742 |           23,957,815 | captured              |
| `title`       |                  40,515 |              882,995 | heading_path          |
| `sec`         |                  25,594 |           70,153,735 | recursed              |
| **`list`**    |              **13,835** |       **11,560,612** | **DROPPED**           |
| `table-wrap`  |                   7,725 |           18,121,344 | captured              |
| `ref-list`    |                   1,025 |           14,846,480 | dropped (intentional) |
| `fig`         |                     765 |              350,545 | dropped (captions)    |
| `label`       |                     298 |                  599 | minor                 |
| **`boxed-text`** |               **25** |           **22,700** | **DROPPED**           |
| **`def-list`** |                **1** |           **72,902** | **DROPPED**           |

**Net medical content lost:** ~12 MB (11.56 MB `<list>` + 73 KB `<def-list>` +
23 KB `<boxed-text>`).

## Concrete patient-affecting example

NBK1247 (BRCA1/BRCA2 HBOC) — `<sec>` "Prevention of Primary Manifestations":

```
<sec>
  <title>Prevention of Primary Manifestations</title>
  <p>Breast cancer</p>
  <list>
    <list-item>Consider prophylactic bilateral mastectomy. …</list-item>
    …
  </list>
  <p>Ovarian cancer (including fallopian tube cancer)</p>
  <list>
    <list-item>Consider prophylactic salpingo-oophorectomy …</list-item>
    …
  </list>
</sec>
```

Currently produces passage `NBK1247:0022` with text:

```
Breast cancer

Ovarian cancer (including fallopian tube cancer)
```

That is a 63-character header stub.  The actual clinical recommendations
about prophylactic surgery are not in the corpus.  An LLM asked
"what surgical options reduce cancer risk in BRCA1 carriers?" cannot
answer correctly from this server.  This is a medical-content data-loss
bug, not a UX bug.

## Guardrail design (what we add to prevent recurrence)

### 1. Whitelist + denylist, no implicit drop

Replace the `<sec>` child loop's "anything else is intentionally ignored"
with:

- `CAPTURE_TAGS`     = {`p`, `table-wrap`, `sec`, `list`, `def-list`, `boxed-text`}
- `STRUCTURAL_TAGS`  = {`title`, `label`}                   (heading-path only)
- `KNOWN_SKIP_TAGS`  = {`ref-list`, `fig`, `graphic`, `supplementary-material`, `xref`, `disp-formula`, `inline-formula`, `permissions`, `notes`}  (each with a stored reason)
- Anything outside those three sets that contains non-whitespace text
  raises `UnknownNxmlTagError` at ingest, which fails the pipeline.

This converts the failure mode from "silent loss" to "loud crash" the
first time NCBI adds a new element type.

### 2. Per-chapter conservation audit

For each parsed chapter, the chunker emits a per-chapter audit record:

```
{
  "nbk_id": "NBK1247",
  "body_text_chars": 47821,            # sum of <body>.itertext() (normalized)
  "captured_text_chars": 47652,        # sum of passage.text chars
  "skipped_by_tag": {"ref-list": 12345, "fig": 220},
  "unaccounted_chars": 0,              # body - captured - skipped (whitespace-normalized)
  "passage_count": 28,
  "list_passages_emitted": 4,
}
```

Pipeline asserts `unaccounted_chars / body_text_chars < 0.5%`.  Above
that threshold = ingest fails for that chapter.  Audit log lands in a
new table `genereview_ingest_audit` keyed on `(nbk_id, ingested_at)`.

### 3. Schema-discovery test

A pytest scans the gene_NBK1116 tarball's NXMLs (or a frozen test
fixture set) and asserts that every direct-child tag of `<sec>` across
the corpus appears in either `CAPTURE_TAGS`, `STRUCTURAL_TAGS`, or
`KNOWN_SKIP_TAGS`.  If NCBI introduces a new tag in a future release,
CI fails with a clear "review and classify this tag" message.

### 4. Golden-text round-trip test

For 3-5 hand-picked chapters (covering `<list>`, `<def-list>`,
`<boxed-text>`, nested `<list>` inside `<list-item>`, mixed-content
`<sec>`), the test fixture stores a sentence-bag derived from the
expected captured text.  After parsing, assert every expected sentence
appears in at least one passage.text.

### 5. Per-passage content hash + parser version

Each passage row gets `content_sha256` + `parser_version`.  Reingest
with same parser_version on same NXML must produce identical hashes —
catches non-determinism.

### 6. Negative test

Synthetic NXML with `<boxed-text>` containing string
`"GUARDRAIL-CANARY-XYZ"` → parsed corpus must contain a passage with
that string.  Fails loudly if regressed.

### 7. Structured ingest log

Per-chapter audit goes through `logging` at INFO level with
`extra={"nbk_id": ..., "unaccounted_chars": ..., ...}` so operators
can grep for `unaccounted_chars > 0` in production logs.

## What we deliberately keep skipping (with reasons)

| Tag                       | Reason for skip |
|---------------------------|-----------------|
| `ref-list`                | Bibliography — not patient-facing prose; cited via PMID elsewhere |
| `fig` / `graphic`         | Image; captions are short and noisy in retrieval |
| `supplementary-material`  | External file pointers |
| `xref`                    | Cross-reference markers (rendered through itertext on parent) |
| `disp-formula`, `inline-formula` | Mathematical formulae — GeneReviews uses these for unit notation |
| `permissions`             | Copyright/license metadata (we surface this via genereview://license) |
| `notes`                   | Authoring notes, not patient content |

Every entry above must have an explicit reason in code, not just absence
from the capture set.

## Research notes — best practices for guarding against silent loss in
## chunking pipelines

These principles informed the design above.

1. **Conservation invariants over silent passthrough.**  Any pipeline
   that transforms text must be auditable: input_chars ==
   output_chars + accounted_skipped_chars (modulo whitespace).  Without
   that, drift is invisible.
2. **Whitelist what you keep, denylist what you skip, fail on unknown.**
   Implicit "ignore unknown" is how content loss compounds across
   schema upgrades.  Medical/legal/regulatory pipelines should treat
   an unknown element with text as a crash, not a warning.
3. **Schema-discovery tests as CI gates.**  Periodically scan the live
   source corpus, enumerate every tag, fail CI if a new tag isn't
   classified.  Bookshelf/NCBI evolves; the test is the trip wire.
4. **Golden-text round-trip tests.**  Hand-curate small fixtures with
   known content invariants ("this sentence must appear after
   chunking").  Token-bag subset assertions catch regressions even
   when chunk_index/passage_id shuffles.
5. **Canary strings.**  Synthetic test inputs with unique tokens
   ("GUARDRAIL-CANARY-XYZ") force the pipeline to prove it surfaces
   content from each handled tag, not just claim to.
6. **Per-record content hashes.**  Detect non-deterministic parsers
   and silent re-encoding bugs.  Tie the hash to the parser version.
7. **Per-chapter audit logs persisted to a table.**  Not just stdout —
   queryable history of every ingest, so post-hoc you can answer
   "when did chapter X start losing content?"
8. **Coverage as a metric, not a binary.**  `captured / source` ratio
   per chapter belongs on a dashboard alongside p99 latency.  A drop
   from 0.99 → 0.94 is a red flag long before users notice.
9. **Version every parser-dependent artifact.**  Embeddings, passage
   ids, search snippets all derive from the parser.  Bumping
   parser_version in `corpus_version` lets downstream caches invalidate
   correctly.
10. **Explicit policy, not implicit habit.**  Skip decisions need a
    reason in code (`# skipped: bibliographic refs, not patient prose`)
    so future maintainers know *why* and can re-evaluate when policy
    shifts.

References that informed this:

- NCBI JATS DTD (`https://jats.nlm.nih.gov/`) — element semantics for
  `<list>`, `<def-list>`, `<boxed-text>`, etc.
- Anthropic / OpenAI / LlamaIndex retrieval-pipeline guidance:
  defensive parsing, content-preservation invariants, observability of
  ETL steps for RAG corpora.
- HIPAA-adjacent ETL playbooks: "data loss must be detectable, not just
  recoverable."

## Open scope question (asked of user before reingest)

We will need a full reingest after the fix; `corpus_version` bumps and
passage IDs renumber for any chapter containing `<list>` content.  This
is a soft break for clients caching numeric IDs.  Citations via
`recommended_citation` still resolve.  Worth flagging in the next PR
description.
