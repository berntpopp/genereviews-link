---
date: 2026-05-12
session: CF (Cystic Fibrosis) grounding session
consumer: LLM client (read-only review)
target: PR #11 (feat/mcp-llm-ergonomics, head c9cd08f at review time)
---

# GeneReview-Link MCP — LLM Consumer Rating (Review #3)

> Historical record

> Honest scores from this session's actual use, plus concrete improvement asks.

## Scores

| Dimension | Score | Notes |
| --- | ---: | --- |
| Tool naming & discoverability | 8/10 | Names are predictable (`search_passages`, `get_passage`, `get_table`). Deferred-by-default + ToolSearch works but adds a round trip before I can plan. |
| Tool descriptions | 9/10 | Excellent. Each description tells me when to pick this tool, when to switch modes, what the latency looks like, and which params to filter on. The latency hints (e.g., "~27ms p50") are unusually helpful. |
| Parameter schemas | 9/10 | JSONSchema enums for `section`, `rerank`, `mode`, `include` mean I don't have to guess. Regex patterns on `nbk_id` and `passage_id` catch malformed IDs early. |
| Response envelope / token economy | 9/10 | `mode=brief` with `ts_headline` snippets is the right default — triage in ~3KB, then opt into `mode=full`. `include`/`exclude` for projection is a nice touch. |
| Error messages & recovery | 10/10 | The `get_table('t6')` 404 was exemplary: machine-readable code, human message, `recovery_hint`, `field_errors[].valid_values` with the full table-ID list, and `next_commands` pointing me at the fix. This is best-in-class. |
| Citation / provenance | 9/10 | Stable `passage_id` (`NBKxxxx:NNNN`) + `chapter_last_updated` makes citing trivial. `_meta.attribution` + a dedicated `genereview://license` resource is the right pattern. |
| Server instructions block | 8/10 | Dense but useful — it taught me the canonical pipeline (search → metadata → passage/section/table) in one read. Got truncated in my context though; could be tighter. |
| Workflow ergonomics | 8/10 | The brief→metadata→full progression is natural. One friction point: I had to guess a table ID (`t6`) because metadata didn't expose them — the error rescued me, but a metadata fix would skip the round trip. |
| Safety scoping | 9/10 | Clear research-use framing, license attribution on every response, no destructive operations exposed. |
| Determinism / reproducibility | 8/10 | `corpus_version` stamped on `_meta` is great. RRF + dense rerank is non-trivial to reproduce externally, but `rerank=lexical`/`off` give me debugging escape hatches. |

**Overall: 8.7/10** — one of the more LLM-thoughtful MCPs I've used this session.

## Concrete improvement suggestions

1. **List table IDs in `get_chapter_metadata`.** Today metadata gives `table_count: 12` but not the IDs. The 404 from `get_table('t6')` showed the canonical IDs are slugs like `cf.T.cystic_fibrosis_targeted_therapies` — I'd never have guessed those without the error. Add a `tables: [{table_id, caption, section, heading_path}]` array to metadata.

2. **List section IDs the same way.** `sections[].section` exists but tables are keyed by the slug form (`cf.T.…`, `cf.molgen.TA`). Surface both forms (numeric "Table 6" and stable slug) consistently in search hits and metadata so I can round-trip between them.

3. **Unify text field naming across modes.** `search_passages` brief mode returns `text: null + snippet: "..."`, full mode returns `text: "..." + snippet: null`. A single `text` field with a `text_kind: "snippet"|"full"` discriminator would be cleaner for downstream parsing.

4. **Populate `summary` section or document its absence.** NBK1250's metadata shows `summary: passage_count 0`, which surprised me — CF clearly has a summary on Bookshelf. Either expose it or add a `_meta.note` explaining the chunking policy (e.g., "summary merged into other").

5. **Add a one-shot "answer a gene-disease question" tool.** Equivalent to PubTator-Link's `ground_question`. For 80% of queries I'm doing the same search → metadata → 1-3 passages dance; a server-side composite (`answer_question(q, gene?, sections?)`) returning a small evidence pack with citations would cut 3-4 round trips to 1. Keep the granular tools for control.

6. **Trim the server instructions block, or split it.** The instructions are excellent but long enough that they got truncated in my system reminder. Consider moving the long-form guidance to an MCP resource (`genereview://usage`) and keeping instructions to a ~200-word quickstart with a pointer.

7. **Make `mode=brief` return `passage_type` consistently in the snippet path.** It already does — keep it. Bonus: for `passage_type=table` hits, include `table_id` directly in the search result so I can jump to `get_table` without parsing the `heading_path`.

8. **Surface "responsive variant" classification programmatically.** The therapy table refers to "responsive CFTR pathogenic variant" with footnotes. A `get_variant(nbk_id, hgvs)` or a `responsive_variants` field on the targeted-therapy table would let me answer modulator-eligibility questions without parsing prose footnotes.

9. **Add `cite()` helper or `recommended_citation` field.** `_meta.attribution` is good; a per-passage `recommended_citation` string ("Cystic Fibrosis. NBK1250. Updated 2024-08-08. Passage NBK1250:0053.") would standardize my output.

10. **Diagnostic preview for empty results.** The instructions promise `_meta.diagnostic…` hints on empty searches — I didn't trigger that path here, but exposing it in non-empty paths as `_meta.suggestions` (e.g., "you searched without a section filter; consider `sections=['management']`") would help me self-correct.

**Smallest highest-leverage win:** #1 (tables in metadata) — eliminates the most common avoidable round trip.

## Resolution in Pass-3-A spec

Mapped into `docs/superpowers/specs/2026-05-12-mcp-llm-ergonomics-pass3a-design.md`:

- **#1, #2 (tables list + slug naming):** Task C1 (TableSummary with `heading_path`) + Task A1 (usage resource "Table ID naming" section).
- **#4 (summary section explanation):** Task C3 (`notes: list[str]` populated by `SYSTEMATICALLY_UNSCRAPED_SECTIONS` rule).
- **#6 (instructions split):** Tasks A1–A3 (trim + `genereview://usage` resource).
- **#7 (`table_id` on search hits):** Task J1.
- **#9 (`recommended_citation`):** Task J1.

Deferred to Pass-3-B or later:

- **#3 (`text_kind` discriminator):** Breaking change; out of Pass-3-A's zero-breaking-change envelope.
- **#5 (`answer_question()` composite):** Larger scope; overlaps with Pass-3-B's `cite()` helper and may grow into Pass-3-C.
- **#8 (`get_variant()` / responsive_variants):** Content-pipeline / domain-extraction work, not ergonomics.
- **#10 (proactive diagnostics on non-empty results):** Requires signal-detection logic; Pass-3-B.
