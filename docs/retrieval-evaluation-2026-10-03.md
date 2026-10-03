# GeneReviews retrieval evaluation, 2026-10-03

## Changes under review

Free-text search now recognizes a single conservative, exact gene-symbol token and uses
the chapter's indexed gene metadata to rank relevant candidates. The lookup is a ranking
hint: only the explicit `gene=` parameter filters chapters. Candidate augmentation retains
the original query filters, limits matching to defining chapter metadata, and never treats
an arbitrary body mention as proof of a chapter's gene. Ambiguous common words and
multi-gene comparisons do not select one gene. Dense-only results now contribute only a
dense vote to reciprocal-rank fusion.

Corpus evaluation records an algorithm identifier. A manifest created before this change
has no identifier and is replayed by the exact legacy lexical evaluator. New manifests use
`primary-gene-aware-lexical-v2`. The BGE model identity in the evaluation record describes
the corpus's stored embeddings; the frozen evaluator itself is lexical and does not claim
to use those embeddings. Unknown evaluation identifiers are rejected.

## Frozen corpus gate

The 2026-10-03 candidate's V2 lexical evaluation returned MRR@10 `0.295238`, with the
existing section-hit@5 field at `0.8` across five queries. Both established floors remain
unchanged. The published September release replays under the legacy algorithm to its
original result object and digest (`0.261905` MRR@10, `0.4` section-hit@5); the October
candidate under legacy semantics scores `0.253571` and does not meet the old MRR floor.
That is why the manifest carries a closed-world algorithm version instead of rewriting
historical results.

The existing `section_precision_at_5` field is named too broadly: its implementation
measures whether any result in the top five has the expected section, regardless of
chapter. It is retained for compatibility and is not interpreted as chapter-and-section
joint relevance. A separate review of five frozen cases found seven relevant
chapter-and-section passages among 25 top-five slots (`0.28`) on both the prior and
candidate production-route captures.

## Production route probe

The exact October candidate dump was restored into a fresh PostgreSQL 18.6 / pgvector 0.8.6
database, and the HNSW index was rebuilt from the restored embeddings. The existing pinned
BGE-small ONNX model ran with `CPUExecutionProvider`; no passage embeddings were generated.
Ten selected biological queries covered BRCA1 and BRCA2 surgery, Huntington disease,
cystic fibrosis and CFTR therapy, HFE, and GRIN2B. Query execution was serial.

Under this ten-query probe's descriptive relevance labels, target MRR@10 was `0.775`,
target recall@10 was `1.0`, and the top-five target precision was `0.56` (28 relevant
results in 50 slots). Chapter-level targets count any passage from that chapter; exact
passage probes count only their reviewed passage. Local latency was 244 ms median and
762 ms maximum. Three repeats each of Huntington genetic counseling, BRCA1 risk-reducing
surgery, and CFTR modulator therapy returned identical top-ten order and dense scores;
dense similarity decreased monotonically by dense rank.

These selected queries are a diagnostic probe, not a representative benchmark or an
out-of-domain evaluation. In particular, the section-level frozen labels, chapter-level
probe labels, and exact-passage labels answer different questions. A broader, independently
curated corpus with adjudicated passage relevance and cross-gene negative examples is still
needed before making a general quality or state-of-the-art claim. This change does not
compare alternative models or add a reranker.

The CFTR exact-passage target used by an older diagnostic record, `NBK1250:0032`, was
re-chunked in the October source snapshot. The current passage for the same Treatment of
Manifestations / Targeted Therapies / Table 6 evidence is `NBK1250:0034`; current-source
evaluation uses that identifier and retains the old identifier in the evidence history.

## Runtime compatibility check

The exact pinned server image creates pgvector 0.8.6 on a new volume and contains no 0.8.2
extension package. On a fresh, resource-limited PostgreSQL 18.6 instance, current migrations
installed 0.8.6, the exact published September dump restored with all 890 chapters, 41,414
passages, and 41,414 embeddings, and the rebuilt HNSW index passed native bundle validation.
The October candidate restored with 892 chapters, 41,568 passages, and 41,568 embeddings
and also passed. Both restores retained their original 0.8.2 computation records. Restore
compatibility is explicitly directional: 0.8.2→0.8.2, 0.8.2→0.8.6, and 0.8.6→0.8.6.
The downgrade path and unknown versions are rejected.

Reproducible, sanitized outputs for the cold restore, route probe, repeatability probe, and
negative manifest checks are kept with the campaign evidence under `audit-b/`.
