# Ranking Benchmark Generation Prompt (Codex)

This is the exact prompt template passed to Codex (via `codex:rescue`) to generate clinical queries with known gold passages for the GeneReviews-Link ranking benchmark.

**Model used at generation time:** record actual model + date in `ranking_bench.jsonl` `meta` field at top of file.

**Generation invocation:**
```bash
codex task -c model_reasoning_effort=medium <<'PROMPT'
[contents of this file with {PASSAGE_BLOCK} substituted]
PROMPT
```

## The prompt

```xml
<task>
You are given a passage from an NCBI GeneReviews chapter.
Generate 2 clinical-research queries that a clinician, researcher, or LLM agent
would use to retrieve this specific passage from a search index.

Requirements per query:
- Specific enough that this passage is the natural answer.
- Phrased like a real LLM-agent search query — not an exam question.
- Different in surface form (one keyword-style, one natural-language).
- Do NOT include the exact passage text verbatim.
- If the passage is short (<200 chars) and is itself a redirect (e.g., "See Management..."),
  set "skip": true with reason "cross_reference_passage".

Passage:
nbk_id: {NBK_ID}
chapter_title: {CHAPTER_TITLE}
section: {SECTION}
heading_path: {HEADING_PATH}
passage_id: {PASSAGE_ID}
text: |
  {PASSAGE_TEXT}
</task>

<structured_output_contract>
Return ONLY a JSON object (no commentary, no markdown fence) with exactly this shape:
{
  "skip": false,
  "queries": [
    {
      "query": "...",
      "intent": "management" | "diagnosis" | "genetics" | "prognosis" | "phenotype" | "other",
      "style": "keyword" | "natural_language"
    },
    {
      "query": "...",
      "intent": "...",
      "style": "..."
    }
  ]
}

If skip: true, return:
{ "skip": true, "reason": "<one-of-known-reasons>" }
</structured_output_contract>

<grounding_rules>
Only generate queries answerable by THIS passage. If the passage is a list,
a cross-reference, or a definition without enough specificity to be retrieved,
set skip: true. Do not invent clinical facts not present in the passage text.
</grounding_rules>
```
