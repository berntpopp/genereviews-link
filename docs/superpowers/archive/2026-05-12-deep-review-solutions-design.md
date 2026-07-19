# Deep Review Engineering Solutions — Design Spec (rev 2)

**Date:** 2026-05-12 (rev 2: senior-review corrections applied)
> Historical record

**Author:** senior MCP engineer (Opus 4.7, deep-review session)
**Source review:** `docs/superpowers/reviews/2026-05-12-mcp-llm-deep-toolset-review.md`
**Scope:** concrete code-level fixes for all 13 bugs (B1–B13) and the 8 consensus improvements from the cross-LLM synthesis, with file:line refs, code sketches, effort estimates, and external-research citations.
**Branch target:** sequential batches off `main` after PR for current `feat/ranking-quality-v1` merges.

---

## Revision history (rev 2 → rev 1)

A senior peer review of rev 1 caught several errors against the live worktree. Every change below is reflected in the relevant bug section. **Workers should use rev 2 sections exclusively; rev 1 sketches contained bugs.**

| Bug | rev 1 error | rev 2 correction |
|---|---|---|
| **B1** | "Add `nbk_id`" — insufficient because `RankedPassage` *also* requires `chapter_title`, `char_count`, `recommended_citation`, `source_url` (no defaults). | Define an explicit `IdsOnlyPassage` slim model + `IdsOnlySearchResponse` envelope. Tool returns the union shape. Test asserts via the MCP runtime path, not just FastAPI. |
| **B3** | Proposed changing `LinkData.urls` from `list[str]` to `list[LinkEntry]` — **breaking** API change. | Keep `urls: list[str]` (back-compat). Add `link_entries: list[LinkEntry] | None` and `by_type: dict[str, list[str]]` as additive fields. |
| **B4** | Root cause sketch was incomplete (described "double emission" but the actual bug is `find_all()` walking *all descendants* — children, grandchildren, and tag-nested content all collected). | Keep the section dict shape unchanged; fix only the walk to use direct children + explicit recursion into the h3 sibling region, with `seen_nodes` guard. |
| **B5** | Proposed new column `chapters.indexed_at`. | `genereview_chapters.ingested_at` already exists at [`0001_chapters.sql:14`](../../genereview_link/db/migrations/data/0001_chapters.sql). Expose it as `chapter_ingested_at` in metadata and ranked-row models, no migration needed. |
| **B6** | Rowspan algorithm stored `(value, rowspan)` — should store `(value, rowspan-1)` because the current row consumed one span. Also `tr.findall("td") + tr.findall("th")` reordered mixed cells. | Iterate cell children in source order via `tr` direct children; store `remaining = rowspan - 1` (or skip storing if `rowspan == 1`). |
| **B7** | "Wire passage_role" + classifier-tuning sketch — both wrong scope. The real failure is structural: today's pipeline gates dense scoring behind lexical-top-50, so passages at lexical rank ≥51 (hemochromatosis #66, MCAD #114) never reach the rerank stage at all. No classifier tuning can rescue them. | **B7 removed from this spec entirely.** Moved to a separate `ranking-architecture-redesign` design doc (TBW) that will cover proper independent hybrid retrieval (lexical-top-K ∪ dense-top-K corpus-wide → RRF → optional cross-encoder). |
| **B8** | SQL sketch used `recall_overlap_count` (a SELECT-list alias) in the same query's `WHERE`. Also used whitespace token count for coverage. | Use a CTE so `recall_overlap_count` is available downstream; compute coverage from the normalized `recall_terms` array (the actual tsquery lexemes), not whitespace tokens. |
| **B10** | `meta: ResponseMeta` would emit JSON key `"meta"`, not `"_meta"`. | Use `meta: ResponseMeta = Field(alias="_meta", ...)` + `model_config = {"populate_by_name": True}`, matching the existing `PassageSearchResponse` pattern at [`genereview_models.py:287`](../../genereview_link/models/genereview_models.py). |
| **B11** | Code sketch added `note` to the return — but `ChapterSectionResponse` doesn't have a `note` field. Also, when passages is empty, the route has no `head` row for `chapter_title` / `chapter_last_updated`. | Add `note: str \| None = None` to `ChapterSectionResponse`. Fetch chapter metadata separately when `passages` is empty, then return 200 with the note. |
| **B12** | `if not matched:` was global — after the first matching token, later unmatched tokens skipped fuzzy fallback. | Track matches per token; fall back to fuzzy only for that specific unmatched token. |
| **C1** | Proposed adding a `get_license` MCP tool, but [`server_manager.py:444`](../../genereview_link/server_manager.py) deliberately excludes `/license` from MCP tools (it is served as `genereview://license` resource only). | Two valid paths: (a) remove the exclusion and update tests; (b) keep the resource-only policy and just add a one-line description hint pointing LLMs at the resource. **Pick (a)** to honor the 3-of-5 reviewer consensus, and update the existing exclusion test. |
| **MCP framing** | Treated B1 as a definite MCP runtime error. | Frame as a schema-contract mismatch: the MCP `outputSchema` per the [spec](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) is enforced by FastMCP from the return type. The behavior is reproducible by inspecting the generated schema; the test should assert the generated schema accepts the actual response shape. |

---

## Goal

Lift this MCP's cross-LLM consumer rating from **8.62/10** to **≥9.3/10** by:

1. Fixing the **1 ship-blocker** (B1 `mode=ids_only`),
2. Eliminating the **4 high-severity parser bugs** on the corpus + E-utils path (B2/B3/B4/B6),
3. Closing the **1 data-integrity gap** (B5 corpus freshness),
4. Tightening the lexical mode safety net (B8) and resolving 5 lower-severity consistency issues (B9–B13),
5. Implementing the **2 high-consensus ergonomic improvements** that 3+ independent LLM reviewers requested (`get_license` tool, lead-with-affordance description copy).

**Out of scope for this spec.** B7 (cross-reference at rank #1) was originally planned here but is being moved to a separate **ranking architecture redesign** spec, because the failure is structural: today's pipeline gates dense scoring behind the top-50 lexical candidates, so passages outside that window (hemochromatosis #66, MCAD #114) cannot be rescued by any role/intent tuning. The rank-disagreement diagnostic (consensus item #5) also moves to that spec, since it pairs naturally with the redesigned retrieval signals.

The 4-agent code-mapping pass + 8 web-research queries that produced this spec are summarized at the end ("Research and code-mapping methodology").

---

## Phasing

Three batches, gated independently, each lands with `make ci-local` clean before the next starts:

| Batch | Theme | Bugs/items | Effort | Risk |
|---|---|---|---|---|
| **C1 — Ship-blockers + parser fixes** | unblock advertised modes, fix data-integrity gaps | B1, B6, B11, B13 | S–M | low (additive, no reingest) |
| **C2 — Freshness + lexical safety net** | expose `chapter_ingested_at`, tighten lexical multi-token degradation | B5, B8 | S–M | low (additive; no rerank SQL changes) |
| **C3 — Legacy path + ergonomics** | fix or formally deprecate E-utils/scraper path; finish low-severity items; ship `get_license` tool wrapper | B2, B3, B4, B9, B12 + consensus items #3, #4 | M | low (legacy-path isolated) |

B7 (ranking architecture) is **not** in this spec's batches and will be tracked separately. See [B7 section below](#b7--default-rrf-surfaces-cross-reference-passages-at-rank-1--deferred-to-separate-spec) for the deferral note.

---

## Per-bug solutions

For each bug: **Root cause** (with `file:line` from the 4 code-mapping agents), **Solution** (code sketch), **Tests** (new + updated), **Effort**.

### B1 — `mode=ids_only` returns "Output validation error: 'nbk_id' is a required property" (REV 2)

**Root cause.** The MCP layer derives its `outputSchema` ([MCP spec, `tools` section](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)) from the FastAPI route's `response_model=PassageSearchResponse`. That envelope nests `RankedPassage` ([`genereview_models.py:201-236`](../../genereview_link/models/genereview_models.py)), which requires **seven fields with no defaults**: `passage_id`, `nbk_id`, `chapter_title`, `chapter_section`, `char_count`, `recommended_citation`, `source_url`. The `ids_only` branch at [`passages.py:368-383`](../../genereview_link/api/routes/passages.py) emits only 4 keys via raw `JSONResponse`. FastAPI bypasses response-model validation on `JSONResponse`, which is why `tests/test_routes_passages.py:906` passes. But the FastMCP layer auto-generates its tool `outputSchema` from the declared `response_model` and validates inbound results against it ([FastMCP tools docs](https://gofastmcp.com/v2/servers/tools)). The advertised 4-key shape does not match the 7-required-fields schema, hence the runtime error.

**Solution.** Adding `nbk_id` alone is insufficient — five other fields would still be missing. The clean fix is an **explicit slim model + envelope**, returned via a typed Union from the route:

```python
# genereview_link/models/genereview_models.py — new models, additive
class IdsOnlyPassage(BaseModel):
    """Lean row shape for search_passages(mode='ids_only').

    Exactly the shape documented in genereview://usage; nothing else.
    """
    passage_id: str
    nbk_id: str  # derived at construction time from passage_id
    chapter_section: SectionName
    rrf_score: float | None = None
    lexical_rank_position: int | None = None


class IdsOnlySearchResponse(BaseModel):
    """Envelope returned by GET /passages/search when mode=ids_only.

    Distinct from PassageSearchResponse so the MCP outputSchema is exact.
    """
    results: list[IdsOnlyPassage]
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)
    model_config = {"populate_by_name": True}
```

Route handler returns a Union:

```python
# api/routes/passages.py — typing only, plus build IdsOnly rows when mode=ids_only
async def search_passages(...) -> PassageSearchResponse | IdsOnlySearchResponse:
    # ... existing setup ...
    if mode == "ids_only":
        rows = [
            IdsOnlyPassage(
                passage_id=row.passage_id,
                nbk_id=row.passage_id.split(":", 1)[0],
                chapter_section=row.chapter_section,
                rrf_score=row.rrf_score,
                lexical_rank_position=row.lexical_rank_position,
            )
            for row in ranked
        ]
        return IdsOnlySearchResponse(results=rows, meta=_build_meta(...))
    # ... existing full/brief path returns PassageSearchResponse ...
```

`response_model=None` (let the return type drive schema generation), or use `response_model=PassageSearchResponse | IdsOnlySearchResponse` explicitly so OpenAPI also reflects the union.

**Why the slim model and not optional fields on `RankedPassage`.** Two reasons: (a) keeping `chapter_title`/`recommended_citation`/`source_url` required on `RankedPassage` preserves the citation-correctness guarantee for the default `brief`/`full` modes; (b) the MCP `outputSchema` becomes a union of two well-typed shapes, which is more useful to LLM consumers than a single shape with five "sometimes-null" fields.

**Tests.**
- Add `tests/test_mcp_search_passages_params.py::test_ids_only_output_schema_exact_keys` that calls FastMCP's tool layer (not the FastAPI raw route — the rev 1 mistake was bypassing the actual MCP validator). Assert the response contains exactly `{passage_id, nbk_id, chapter_section, rrf_score, lexical_rank_position}` and that the generated `outputSchema` contains no other required keys.
- Update `tests/test_routes_passages.py:906` to assert the new envelope shape (slim list + `_meta`).
- Add `tests/test_response_envelope_models.py` test that `IdsOnlySearchResponse` round-trips through Pydantic + FastMCP's schema generator.

**Effort:** S–M. Two new models + route Union typing + 3 test variants. Larger than the rev-1 estimate because the test must exercise the MCP runtime, not just FastAPI.

---

### B2 — `get_abstract` returns empty `title` and truncated `abstract` for GeneReviews PMIDs

**Root cause.** PMID 20301425 is a *PubmedBookArticle* (GeneReviews chapter), not a regular PubmedArticle. The parser at [`eutils_client.py:439-517`](../../genereview_link/api/eutils_client.py) (`_parse_book_article`) is split from `_parse_regular_article` ([369-437](../../genereview_link/api/eutils_client.py)). Two issues:

1. **Title (line 453-455)**: uses a single `.find(".//ArticleTitle")` lookup, but GeneReviews book records carry the title in `<BookTitle>` or `<ArticleTitle>` depending on which PubMed XML revision NCBI returns. When NCBI returns a `<BookTitle>` shape (which happens for newer GeneReviews ingestions), the regular-article XPath misses it.
2. **Abstract (lines 458-468)**: iterates `<AbstractText>` elements and joins them with space, but does not preserve the `Label` attribute (`DIAGNOSIS/TESTING`, `GENETIC COUNSELING`, etc.). When abstract has multiple labeled sections, the join is shallow and gets cut off if any element's `.text` is empty but its children carry content (a common pattern: `<AbstractText Label="GENETIC COUNSELING"><i>This GeneReview</i> describes...`).

**Solution.**

```python
# eutils_client.py, _parse_book_article (replace lines 453-468 region)
def _parse_book_article(self, article: ET.Element) -> AbstractData:
    # Title: try BookTitle, then ArticleTitle, then book chapter title
    title_elem = (
        article.find(".//BookTitle")
        or article.find(".//ArticleTitle")
        or article.find(".//Book/BookTitle")
    )
    title = _itertext(title_elem) if title_elem is not None else ""

    # Abstract: preserve Label sections; itertext() walks all child text nodes
    abstract_parts: list[str] = []
    for abstract_text in article.findall(".//AbstractText"):
        label = abstract_text.get("Label") or abstract_text.get("NlmCategory")
        content = _itertext(abstract_text).strip()
        if not content:
            continue
        abstract_parts.append(f"{label}: {content}" if label else content)
    abstract = "\n\n".join(abstract_parts)

    # ... rest unchanged
```

Where `_itertext()` is a small helper that walks the element tree (`"".join(elem.itertext())`) instead of bare `.text`. The library uses defusedxml per AGENTS.md so `ET` here is `defusedxml.ElementTree`.

Reference: titipata/[pubmed_parser](https://github.com/titipata/pubmed_parser) implements this exact pattern for structured-abstract handling. ([WebSearch ref])

**Tests.**
- Add fixture `tests/fixtures/efetch/NBK1247_book_article.xml` capturing a real GeneReviews PubmedBookArticle response.
- Update `tests/test_routes_with_mocks.py::TestAbstractRoute` to mock this fixture and assert non-empty title and full abstract with section labels.
- Add a regression test for the truncation: assert the abstract ends with a complete sentence (no trailing colon or partial word).

**Effort:** M. Parser logic change + fixture capture.

---

### B3 — `get_links` returns `urls: []` for valid PMIDs

**Root cause.** [`eutils_client.py:519-532`](../../genereview_link/api/eutils_client.py) (`get_all_links`) walks `.//ObjUrl/Url`. NCBI's elink response with `cmd=prlinks` (the publisher-link variant) does return `ObjUrl`, but the `cmd=llinks` and other variants nest URLs under `LinkSet/LinkSetDb/Link/Id` (linked-resource IDs, not URLs). For GeneReviews PMIDs the most useful links live in the `LinkSet/IdUrlList/IdUrlSet/ObjUrl/Url` chain — note the deeper nesting under `IdUrlSet`. The current `.//ObjUrl/Url` does match arbitrarily deep so it *should* find them, which means the actual cause is one of:

- (a) the elink call uses `dbfrom=pubmed&db=pubmed` (intra-database links) when it should use `dbfrom=pubmed&cmd=prlinks` or `cmd=llinks` for inter-database links; or
- (b) the response IS returning URLs but they are filtered by a downstream sanitizer; or
- (c) the live PMID has no `prlinks` and the test PMID specifically returns an empty `IdUrlSet`.

**Solution.** Three-step debugging fix:

1. **Log the raw elink response body** at DEBUG level (gated behind a debug flag — never enable in production). One call against PMID 20301425 will show which case is hit.
2. **Parse all four standard NCBI link shapes:**

```python
# eutils_client.py, get_all_links - replace lines 519-532
def get_all_links(self, root: ET.Element) -> list[dict]:
    """Categorized link extraction matching NCBI elink response shapes.

    Returns list of {url, link_type, provider} dicts.
    """
    out = []
    # Shape 1: cmd=prlinks (publisher links)
    for obj_url in root.findall(".//IdUrlSet/ObjUrl"):
        url = obj_url.findtext("Url")
        provider = obj_url.findtext("Provider/Name")
        if url:
            out.append({"url": url, "link_type": "prlinks", "provider": provider})
    # Shape 2: cmd=llinks (LinkOut links)
    for obj_url in root.findall(".//ObjUrl"):
        url = obj_url.findtext("Url")
        category = obj_url.findtext("Category")
        if url:
            out.append({"url": url, "link_type": "llinks", "provider": category})
    # Shape 3: cmd=neighbor (related-records IDs — return as NCBI URLs)
    for link in root.findall(".//LinkSetDb/Link"):
        link_id = link.findtext("Id")
        link_name = root.findtext(".//LinkSetDb/LinkName") or ""
        if link_id and "books" in link_name.lower():
            out.append({
                "url": f"https://www.ncbi.nlm.nih.gov/books/{link_id}/",
                "link_type": "books",
                "provider": "NCBI Bookshelf",
            })
    return out
```

3. **Add categorization as additive fields** (REV 2 — do NOT change the existing `urls: list[str]` shape; that would be a breaking API change):

```python
# genereview_models.py — additive only; keep urls: list[str] back-compat
class LinkEntry(BaseModel):
    url: HttpUrl
    link_type: Literal["prlinks", "llinks", "books", "pmc"]
    provider: str | None = None


class LinkData(BaseModel):
    urls: list[str] = Field(default_factory=list)  # UNCHANGED — flat URL strings, back-compat
    link_entries: list[LinkEntry] | None = None  # NEW — categorized view, opt-in
    by_type: dict[str, list[str]] = Field(default_factory=dict)  # NEW — quick type lookup
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta.live_passthrough)  # NEW
    corpus_version: str | None = None  # UNCHANGED
    model_config = {"populate_by_name": True}
```

The route populates all three fields from the same parsed response:

```python
# api/routes/links.py — after parsing
entries = client.get_all_links(root)
return LinkData(
    urls=[e["url"] for e in entries],  # flat list, back-compat
    link_entries=[LinkEntry(**e) for e in entries],  # categorized
    by_type={
        t: [e["url"] for e in entries if e["link_type"] == t]
        for t in {e["link_type"] for e in entries}
    },
)
```

**Tests.**
- Capture real elink responses for PMID 20301425 with three commands (`prlinks`, `llinks`, default) under `tests/fixtures/elink/`.
- Mock each shape and assert correct categorization in `link_entries` and `by_type`.
- Assert `urls: list[str]` is still populated and back-compat clients can read it.
- Assert `meta.attribution` non-empty.

**Effort:** M. Need elink response capture + additive model fields + 6 test variants.

---

### B4 — `get_fulltext` returns same paragraphs 4–6× per response (REV 2)

**Root cause.** [`eutils_client.py:971-1062`](../../genereview_link/api/eutils_client.py) (`_extract_hierarchical_sections`). The duplication has two compounding causes, **not just parent/subsection double emission** (rev 1 understated this):

1. **Descendant walk.** `section_div.find_all(["p", "div", "ul", "ol"])` returns *every* matching descendant — direct paragraphs **plus** paragraphs nested inside subsection `<div>`s. Each nested `<p>` is therefore visited once at the parent section level.
2. **Container `.get_text()` accumulation.** The walk then calls `.get_text()` on `<div>` containers it encounters, which itself joins all descendant text — so paragraph N's text is also emitted as part of the parent `<div>`'s `.get_text()` block.
3. **Separate h3 subsection extraction.** A second pass (`_extract_subsection_content`, lines 1044-1062) walks h3 siblings and extracts their content again into `subsections[h3_key]`. The same `<p>` is therefore emitted both at the parent-section content *and* inside the subsection dict.

Net effect: each paragraph appears up to 4–6× (parent-section-via-direct-find, parent-section-via-container-get-text, subsection, and any intermediate `<div>` containers).

**Solution.** Keep the existing response shape (`sections[section_key]` is a dict with `content`, `subsections`, and any other fields the current implementation emits — do not redefine the structure). Fix only the walk:

```python
# eutils_client.py - _extract_hierarchical_sections (REV 2: keep dict shape)
def _extract_hierarchical_sections(self, soup: BeautifulSoup) -> dict[str, dict]:
    sections: dict[str, dict] = {}
    seen_nodes: set[int] = set()  # dedupe guard by Python id()

    for h2 in soup.find_all("h2"):
        section_key = self._slugify(h2.get_text(strip=True))
        section_div = h2.find_parent("div", class_="sec") or h2.parent
        if section_div is None:
            continue
        if id(section_div) in seen_nodes:
            continue  # outer section already consumed (shared parent)

        # Walk DIRECT children of section_div in source order — never recurse
        # via find_all() which would visit descendants too. Subsections are
        # handled by following next_siblings on h3, not by recursing into
        # section_div with find_all.
        content_blocks: list[str] = []
        subsections: dict[str, list[str]] = {}

        for child in section_div.children:
            if not hasattr(child, "name") or child.name is None:
                continue
            if id(child) in seen_nodes:
                continue
            if child.name == "h2":
                continue  # skip the heading itself
            if child.name == "h3":
                sub_key = self._slugify(child.get_text(strip=True))
                # Collect this h3's siblings up to the next h3 / h2
                sub_blocks = self._collect_until_heading(child, {"h2", "h3"}, seen_nodes)
                subsections[sub_key] = sub_blocks
                seen_nodes.add(id(child))
            elif child.name in {"p", "ul", "ol", "table"}:
                content_blocks.append(child.get_text(separator=" ", strip=True))
                seen_nodes.add(id(child))
            # Crucially: do NOT call `.get_text()` on intermediate <div> containers.
            # If a <div class="sec"> contains <p> children, they'll be picked up
            # in a separate h2-driven iteration of the outer loop.

        # Preserve whatever extra fields the current shape emits (level, title, etc.)
        sections[section_key] = {
            "title": h2.get_text(strip=True),
            "content": "\n\n".join(content_blocks),
            "level": 2,
            "subsections": subsections,
        }
        seen_nodes.add(id(section_div))

    return sections
```

Where `_collect_until_heading(start_elem, stop_tags, seen_nodes)` is a new helper that walks `start_elem.next_siblings` until a stop tag is reached, collecting their text and marking each as seen. This replaces the buggy second pass at lines 1044-1062.

**Key correction vs rev 1.** Rev 1 framed this as "parent walks descendants, subsection walks again" — true but incomplete. The real fix is **never call `.get_text()` on intermediate container divs**, because BS4's `.get_text()` itself joins all descendant text. The recursion happens implicitly through that method. The walk must stay on direct children only.

**Tests.**
- Add `tests/fixtures/bookshelf_html/NBK1247_management.html` captured live.
- Assert `content.count("Consider prophylactic bilateral mastectomy.") == 1`.
- Assert subsection keys do not duplicate top-level content.
- Assert the response **dict shape** matches the current schema (compare a deepdiff of dict keys against the existing fixture to confirm no breaking shape changes).

**Effort:** M. Walk rewrite + new `_collect_until_heading` helper + fixture capture + 4 regression assertions.

---

### B5 — `chapter_last_updated` lags NCBI for NBK1247 (2022-02-03 indexed vs March 25 2026 live) (REV 2)

**Root cause.** [`genereview_link/corpus/nxml.py:179-198`](../../genereview_link/corpus/nxml.py) (extract_dates) + [`nxml.py:298-307`](../../genereview_link/corpus/nxml.py) (`_parse_pub_date`). **The parser logic is correct** (Agent B verified the test at `tests/unit/test_corpus_nxml.py:51-64` passes: `<date date-type="updated">` is preferred over `<date date-type="revised">`). The bug is in the **input** — the indexed NXML for NBK1247 was captured before NCBI's March 2026 update. The chapter content has been partially re-ingested (Schaeffer 2024 reference appears in tables) but the `pub-history` date wasn't refreshed.

**Solution — three-part fix. (REV 2: reuse existing `ingested_at`, no new migration.)**

**B5a — Re-ingest the affected chapters.** The corpus is built from GitHub Release tarballs ([`genereview_link/ingest/github_release.py`](../../genereview_link/ingest/github_release.py)). Trigger a fresh ingest of all chapters with a `pub-history` parse-time more than 90 days older than the GitHub Release timestamp.

**B5b — Expose the existing `ingested_at` column as `chapter_ingested_at` in API models.** The `genereview_chapters` table already has `ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` at [`db/migrations/data/0001_chapters.sql:14`](../../genereview_link/db/migrations/data/0001_chapters.sql). No new migration is needed — just project the column through the repository row and into the response models:

```python
# models/genereview_models.py — additive field on ChapterMetadata and RankedPassage
class ChapterMetadata(BaseModel):
    nbk_id: str
    title: str
    chapter_last_updated: date | None  # editorial date from NXML
    chapter_ingested_at: datetime | None = None  # NEW: from genereview_chapters.ingested_at
    # ... (other existing fields unchanged) ...
```

```python
# retrieval/repository.py - update the SELECT in get_chapter_metadata
# Add c.ingested_at to the column list, project into ChapterMetadata.chapter_ingested_at
```

**B5c — Diagnostic warning when ingested date is unusually old.** Add to `_meta.diagnostics.suggestions` whenever a search response's top-3 hits have `chapter_ingested_at` more than 180 days behind `NOW()`:

```python
# api/routes/passages.py — search_passages handler, after ranked is built
from datetime import datetime, timezone, timedelta

ingest_dates = [r.chapter_ingested_at for r in ranked[:3] if r.chapter_ingested_at]
if ingest_dates:
    oldest = min(ingest_dates)
    if (datetime.now(timezone.utc) - oldest) > timedelta(days=180):
        diagnostics.suggestions.append("corpus-may-be-stale")
```

(Note: this requires `chapter_ingested_at` to also flow through `RankedPassage` — additive field with `None` default.)

**Tests.**
- Update `tests/test_routes_chapter_metadata.py` to assert `chapter_ingested_at` is present and recent.
- Mock a row with `ingested_at` set to 200 days ago and assert the `corpus-may-be-stale` suggestion appears.
- Assert back-compat: existing consumers reading just `chapter_last_updated` continue to work.

**Effort:** S–M. **No migration** (the rev-1 proposal to add `indexed_at` would have duplicated semantics with the existing `ingested_at`). Just model field additions + repository projection + one diagnostic + ingest re-run for the stale chapters.

---

### B6 — `get_table` rowspan parser strips first-column merges

**Root cause.** [`genereview_link/corpus/tables.py:50-51`](../../genereview_link/corpus/tables.py): `rows.append([_text_or_empty(td) for td in tr.findall("td")])`. Naive — no `rowspan` propagation, no `colspan` padding, no inter-row state. Confirmed across `NBK1247` Table 4, `NBK1440` Table 8, `NBK1250` Table 12.

**Solution.** Rewrite `parse_rows` to track pending rowspan cells across rows and expand colspan:

```python
# corpus/tables.py - new parse_rows implementation
def parse_rows(table_elem) -> list[list[str]]:
    """Parse NXML table rows, expanding rowspan/colspan.

    NXML uses NLM/BITS DTD: <td rowspan="N" colspan="M">value</td>.
    rowspan means the value spans N rows downward; colspan means M columns rightward.
    Empty cells are emitted as "" to preserve column alignment.
    """
    rows: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}  # col_idx -> (value, remaining_spans)

    for tr in table_elem.findall(".//tr"):
        row: list[str] = []
        col_idx = 0
        # REV 2: iterate cell children in source order, not findall("td")+findall("th")
        # which concatenates and reorders mixed <th>/<td> within a row.
        cell_iter = iter(c for c in tr if c.tag in ("td", "th"))

        while True:
            # Apply any pending rowspan-merged cells at the current column
            while col_idx in pending:
                value, remaining = pending[col_idx]
                row.append(value)
                if remaining > 1:
                    pending[col_idx] = (value, remaining - 1)
                else:
                    del pending[col_idx]
                col_idx += 1

            # Pull the next cell from the row, if any remain
            cell = next(cell_iter, None)
            if cell is None:
                break

            value = _text_or_empty(cell)
            colspan = max(int(cell.get("colspan", "1") or "1"), 1)
            rowspan = max(int(cell.get("rowspan", "1") or "1"), 1)

            for _ in range(colspan):
                row.append(value)
                # REV 2 BUGFIX: the current row consumed ONE of the rowspan
                # rows. Store remaining = rowspan - 1, not rowspan. The previous
                # value over-extended every rowspan by one row.
                if rowspan > 1:
                    pending[col_idx] = (value, rowspan - 1)
                col_idx += 1

        # If there are pending cells beyond the last <td>, flush them now
        while col_idx in pending:
            value, remaining = pending[col_idx]
            row.append(value)
            if remaining > 1:
                pending[col_idx] = (value, remaining - 1)
            else:
                del pending[col_idx]
            col_idx += 1

        rows.append(row)

    return rows
```

**Why two corrections in rev 2.**
- `tr.findall("td") + tr.findall("th")` puts every `<th>` after every `<td>` regardless of source order; for tables with header cells in the middle of a row (rare but legal in NXML), columns shift. Iterating `tr` directly preserves source order.
- The pending-rowspan store of `rowspan` (instead of `rowspan - 1`) caused merged values to bleed into *one extra* row beyond their declared span. For `rowspan="4"`, the value showed up in rows 1, 2, 3, 4, **and 5**. The off-by-one was silent because most real NXML tables don't immediately re-use the same column after a rowspan, but the bug is real.

**Tests.**
- Add new fixture `tests/fixtures/nxml/table_with_rowspan.nxml` modeled on NBK1247 Table 4 (4 rows where col-1 has `rowspan="4"`).
- Add `tests/unit/test_corpus_tables.py::test_rowspan_propagates` asserting all 4 rows (and *only* those 4) have the merged value.
- Add `tests/unit/test_corpus_tables.py::test_rowspan_does_not_overflow_one_row` asserting row 5 does *not* contain the merged value (catches the rev-1 off-by-one).
- Add `tests/unit/test_corpus_tables.py::test_colspan_expands` asserting a `colspan="2"` cell produces 2 row entries.
- Add `tests/unit/test_corpus_tables.py::test_mixed_th_td_preserves_order` with a row like `<tr><td>A</td><th>B</th><td>C</td></tr>` and assert `["A", "B", "C"]` (catches the rev-1 reordering).
- Update `tests/test_routes_table.py` integration test against re-ingested NBK1247 to assert the surveillance Table 4 row count + column alignment.

**Effort:** M. Algorithm rewrite + 4 new fixtures + ingest re-run for table-bearing chapters.

---

### B7 — Default RRF surfaces cross-reference passages at rank #1 — DEFERRED to separate spec

**This bug is not addressed by this spec.** A senior peer review and follow-up brainstorm established that the cross-reference issue is one *symptom* of a deeper architectural problem: today's pipeline gates dense scoring behind the top-50 lexical candidates, so dense embeddings cannot rescue passages that lexical missed. The hemochromatosis target was at lexical rank 66 and the MCAD target at lexical rank 114 — *no* role multiplier, intent boost, or classifier tuning can promote a passage that was never passed to rerank.

Tuning the role-classifier (the rev-1 + rev-2 plan in earlier drafts of this spec) would address only the BRCA1 case while leaving the rest of the recall-ceiling failures untouched. The right fix is a separate retrieval-architecture redesign — proper independent hybrid retrieval (lexical-top-K ∪ dense-top-K corpus-wide → RRF), optionally followed by a cross-encoder rerank.

**Tracked in:** `docs/superpowers/specs/2026-05-NN-ranking-architecture-redesign-design.md` *(to be written separately, brainstormed with the maintainer)*. That spec will subsume B7, the rank-disagreement diagnostic (consensus item #5), and a forward-looking cross-encoder option.

**What stays in scope here:** nothing. B7 is moved out entirely. The earlier "wire passage_role into rerank" framing is also moot — that wiring already exists at [`rerank.py:35`](../../genereview_link/retrieval/rerank.py) (`ROLE_MULTIPLIER`) and is what the in-flight `feat/ranking-quality-v1` branch (Batch B / Phase 12) is shipping.

---

### B8 — `lexical` rerank degrades sharply with multi-token clinical queries

**Root cause.** [`genereview_link/retrieval/repository.py:185-188`](../../genereview_link/retrieval/repository.py) builds three tsqueries (phrase, strict, recall) from the input. The `recall_tsquery` at [`retrieval/lexical.py:76-81`](../../genereview_link/retrieval/lexical.py) OR-joins all ≥3-char tokens. A query like `"c.5266dupC BRCA1 founder variant Ashkenazi"` tokenizes to `["5266dupc", "brca1", "founder", "variant", "ashkenazi"]` and ORs them together. Any passage matching just `"variant"` or `"founder"` matches.

There IS a penalty at `repository.py:266-269` clamping low-coverage scores to `[0.25, 1.0)`, but it only applies when:
- phrase_rank = 0 AND strict_rank = 0, AND
- query has ≥4 tokens, AND
- `recall_overlap_count <= 1`

So a passage matching 2 unrelated tokens (`variant` + `founder`) skips the penalty. That's why Fukuyama CMD and beta-thalassemia tables surfaced for the BRCA query.

**Solution — three-step (REV 2: rewrite as CTE; base coverage on normalized recall_terms, not whitespace tokens).**

The rev-1 sketch had two SQL errors: (a) `recall_overlap_count` is a select-list alias and cannot be referenced in the same query's `WHERE`; (b) `regexp_split_to_array($2, E'\\s+')` whitespace-tokenizes the *user-supplied query string*, but actual lexical coverage should be measured against the **normalized recall_terms** (the actual `tsquery` lexemes after stop-word removal and stemming).

**B8a — Restructure the candidate query as a CTE so derived columns are usable downstream.**

```sql
-- repository.py — recall candidate selection, REV 2 with CTE
WITH params AS (
  SELECT
    to_tsquery('english_simple', $1) AS phrase_q,
    to_tsquery('english_simple', $2) AS strict_q,
    to_tsquery('english_simple', $3) AS recall_q,
    -- recall_terms is the *normalized* lexeme array, not whitespace tokens
    ARRAY(SELECT unnest(tsvector_to_array(to_tsvector('english_simple', $4)))) AS recall_terms
  -- $1=phrase tsquery, $2=strict tsquery, $3=recall tsquery, $4=original query string
),
scored AS (
  SELECT
    p.passage_id, p.text, p.text_vector, p.passage_role,
    ts_rank_cd(p.text_vector, params.phrase_q) AS phrase_rank,
    ts_rank_cd(p.text_vector, params.strict_q) AS strict_rank,
    ts_rank_cd(p.text_vector, params.recall_q) AS recall_rank,
    -- recall_overlap_count = number of normalized terms matched by the passage
    cardinality(ARRAY(
      SELECT t FROM unnest(params.recall_terms) t
      WHERE p.text_vector @@ to_tsquery('english_simple', t)
    )) AS recall_overlap_count,
    cardinality(params.recall_terms) AS recall_terms_count
  FROM passages p, params
  WHERE p.text_vector @@ params.recall_q
)
SELECT
  *,
  -- B8a — widen the penalty trigger from <=1 to <=2 (low-cov tail)
  CASE
    WHEN phrase_rank = 0 AND strict_rank = 0 AND recall_rank > 0
         AND recall_terms_count >= 4
         AND recall_overlap_count <= 2
    THEN LEAST(1.0, GREATEST(0.25, char_length(text)::double precision / 400.0))
    ELSE 1.0
  END AS coverage_penalty
FROM scored
-- B8b — minimum coverage filter, surgical fix for "Fukuyama for BRCA query"
WHERE recall_overlap_count >= GREATEST(
  1,
  CEIL(0.25 * recall_terms_count)
)
ORDER BY ...
```

Key corrections vs rev 1:
- **CTE structure** — `recall_overlap_count` is now defined in the `scored` CTE and used in both the outer `WHERE` (coverage filter) and the `CASE` (penalty trigger).
- **Coverage measured against normalized lexemes** — `params.recall_terms` is the actual tsquery lexeme array (after stop-word + stemming + dictionary normalization), not naive whitespace tokens. This means `"c.5266dupC BRCA1 founder variant Ashkenazi"` becomes the **lexeme** array (typically 3–5 normalized terms after `c.` is stripped), and "25% coverage" maps to *real* term overlap.
- **`recall_terms_count`** replaces `array_length(regexp_split_to_array($2, E'\\s+'), 1)` — the rev-1 expression and the SQL ordering both depended on a whitespace token count that ignored the engine's actual lexeme normalization.

**B8b — Coverage filter rationale.** A passage matching <25% of normalized recall terms is dropped from the candidate set in `rerank=lexical`. For the BRCA query (5 normalized terms post-stop-word), the threshold is `CEIL(0.25 * 5) = 2`. Passages matching only `"variant"` (1 term, 20% coverage) are dropped; passages matching `"BRCA1" + "Ashkenazi"` (2 terms, 40% coverage) are kept. Tunable; start at 0.25, measure precision/recall on labeled queries before locking.

**B8c — Update the `rerank=lexical` description in [`passages.py`](../../genereview_link/api/routes/passages.py) tool decorator** to add a one-line warning:

> `lexical` (skip the dense pass; lexical scoring only) — Faster, but prefers single-token variant queries. **For multi-token clinical concept queries, use `rrf` (default).** When using `lexical`, supply variant nomenclature alone (e.g., `q="c.5266dupC"`) rather than adding context keywords.

Note: PostgreSQL `tsquery` already normalizes most punctuation; `c.5266dupC` survives tokenization because `c.` becomes a stop-pattern. HGVS-aware tokenization is a deeper improvement (a normalizer that preserves `c.NNN`, `p.X` prefixes and HGVS punctuation) — but that's Batch D scope. ([HGVS Nomenclature](https://hgvs-nomenclature.org/stable/))

**Tests.**
- `tests/test_routes_passages.py` — assert `rerank=lexical` with `q="c.5266dupC BRCA1 founder variant Ashkenazi"` returns ≥1 BRCA1 passage in top 5 (currently returns 0).
- Assert with `q="c.5266dupC"` alone, top hit is `NBK1247:0002` or `NBK1247:0043` (BRCA1 variant passages) — already works pre-fix.
- Assert lexical mode with 5+ unrelated random tokens returns empty or only-high-coverage results.

**Effort:** S. Two SQL clause tweaks + one description string + 3 tests.

---

### B9 — `concatenated_text` overlap default

**Root cause.** [`genereview_link/api/routes/chapters.py:26-49`](../../genereview_link/api/routes/chapters.py) `_strip_overlap()` is the dedupe function. [Line 96](../../genereview_link/api/routes/chapters.py) defaults `dedupe=False`. The test at [`tests/test_chapters_section_route.py:367-381`](../../tests/test_chapters_section_route.py) (`test_dedupe_false_default_preserves_overlap`) asserts the overlap-on default explicitly.

**Solution.** Flip the default:

```python
# chapters.py line 96
dedupe: bool = Query(True, description="..."),  # was False
```

Update the assertion test to its inverse (`test_dedupe_true_default_strips_overlap`). Add an opt-out test (`?dedupe=false`) for the literal-text use case (which exists — corpus auditing tools may need the raw chunk text).

**Document the change** in `genereview://usage` so consumers know the behavior flipped.

**Effort:** XS. One-line default change + test inversion + doc note.

---

### B10 — E-utils tools lack `_meta.attribution`

**Root cause.** [`models/genereview_models.py:43-101`](../../genereview_link/models/genereview_models.py) — `AbstractData`, `LinkData`, `FullTextData` carry only `corpus_version | None` and no `ResponseMeta`. Corpus tools all carry `_meta.attribution` from `ResponseMeta` ([line 272-280](../../genereview_link/models/genereview_models.py)).

**Solution.** Add `meta: ResponseMeta` to all three models. The E-utils responses have no `corpus_version` (they're live passthroughs), so the `ResponseMeta` constructor needs to accept `corpus_version=None` while still populating `attribution` and `license_summary`. Already supported per the existing `ResponseMeta` model.

```python
# genereview_models.py — REV 2: use Field(alias="_meta") to emit "_meta" in JSON
class AbstractData(BaseModel):
    # ... existing fields ...
    meta: ResponseMeta = Field(
        alias="_meta",
        default_factory=lambda: ResponseMeta.live_passthrough(),
    )
    model_config = {"populate_by_name": True}


# ResponseMeta - add factory
@classmethod
def live_passthrough(cls) -> "ResponseMeta":
    return cls(
        # attribution + license_summary already default to ATTRIBUTION_TEXT
        # and "Research use only; ..." per ResponseMeta defaults at line 275-278.
        corpus_version=None,  # explicit — no corpus tied to a live passthrough
    )
```

Apply the same `Field(alias="_meta")` + `model_config = {"populate_by_name": True}` pattern to `AbstractData`, `LinkData`, and `FullTextData`. This matches the existing pattern at [`PassageSearchResponse:287`](../../genereview_link/models/genereview_models.py) and [`PassageWindowResponse:299`](../../genereview_link/models/genereview_models.py), so the emitted JSON key is `_meta` (with leading underscore), consistent with the corpus tools. Without `alias="_meta"`, the JSON would carry `"meta"` and LLM consumers would have to special-case it.

**Effort:** XS. Three model field additions + one factory method + 3 tests asserting the emitted key is `_meta` (not `meta`).

---

### B11 — `get_chapter_section(summary)` returns 404 inconsistent with metadata's 200 (REV 2)

**Root cause.** Section endpoint at [`chapters.py:120-137`](../../genereview_link/api/routes/chapters.py) raises 404 `code=section_empty_for_chapter` when `passages` is empty. Metadata endpoint at [`repository.py:515-601`](../../genereview_link/retrieval/repository.py) returns 200 with `passage_count: 0` and a `note` populated via `_note_for_empty_section()` at [`repository.py:677-687`](../../genereview_link/retrieval/repository.py). The `SYSTEMATICALLY_UNSCRAPED_SECTIONS` constant at [`models/sections.py:27`](../../genereview_link/models/sections.py) currently contains `{"summary"}`.

**REV 2 corrections.** Rev 1 contained two coding bugs:
1. **`ChapterSectionResponse` does NOT currently have a `note` field** ([`genereview_models.py:314-324`](../../genereview_link/models/genereview_models.py) — only `nbk_id`, `chapter_title`, `chapter_section`, `chapter_last_updated`, `passages`, `passage_count`, `concatenated_text`, `concatenated_char_count`). It must be added.
2. **When `passages` is empty there is no `head = passages[0]`** ([`chapters.py:138`](../../genereview_link/api/routes/chapters.py)) — so we cannot derive `chapter_title` or `chapter_last_updated` from it. We must fetch chapter metadata separately.

**Solution (REV 2 — two changes).**

**B11a — Add `note: str | None = None` to `ChapterSectionResponse`** and bump the model so empty-section responses can carry the documented note:

```python
# models/genereview_models.py — additive on ChapterSectionResponse
class ChapterSectionResponse(BaseModel):
    nbk_id: str
    chapter_title: str
    chapter_section: SectionName
    chapter_last_updated: date | None = None
    passages: list[PassageInSection]
    passage_count: int
    concatenated_text: str | None = None
    concatenated_char_count: int | None = None
    note: str | None = None  # NEW — populated for systematically unscraped sections
```

**B11b — Fetch chapter metadata when `passages` is empty, then return 200 for systematically unscraped sections.** Since there's no head row, look up the chapter's title and last_updated date via the existing `get_chapter_by_nbk()` repository function (or a similar light-weight head query):

```python
# api/routes/chapters.py — replace the 404-throw block at lines 120-137
from genereview_link.models.sections import SYSTEMATICALLY_UNSCRAPED_SECTIONS
from genereview_link.retrieval.repository import _note_for_empty_section

passages = await repo.get_section(nbk_id, section, heading_path_contains=heading_path_contains)

if not passages:
    # Fetch chapter metadata so we have title + last_updated for the 200-OK response.
    chapter = await repo.get_chapter_by_nbk(nbk_id)
    if chapter is None:
        # Chapter itself doesn't exist — surface as the existing chapter_not_found 404.
        raise StructuredHTTPException(
            status_code=404,
            code="chapter_not_found",
            message=f"chapter {nbk_id!r} not in corpus",
            ...
        )
    if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
        # Documented absence — return 200 with empty passages + note,
        # mirroring get_chapter_metadata's behavior for this section.
        return ChapterSectionResponse(
            nbk_id=nbk_id,
            chapter_title=chapter.title,
            chapter_section=section,
            chapter_last_updated=chapter.last_updated,
            passages=[],
            passage_count=0,
            note=_note_for_empty_section(section, nbk_id),
        )
    # Truly empty (chapter exists, section is scraped but has 0 rows) — keep the 404 as before.
    raise StructuredHTTPException(
        status_code=404,
        code="section_empty_for_chapter",
        message=f"chapter {nbk_id!r} has no passages in section {section!r}",
        recovery_hint=...
    )
```

The rev-1 sketch failed because it referenced `chapter.title` / `chapter.last_updated` without having fetched the chapter at all (rev-1 used a non-existent `head` variable in the empty-passages branch).

**Tests.**
- `tests/test_chapters_section_route.py::test_summary_section_returns_200_with_note` — `get_chapter_section(NBK1247, summary)` → 200, `passages=[]`, `note` non-empty and points to the NCBI URL.
- `test_response_envelope_models.py::test_chapter_section_response_has_note_field` — schema assertion.
- `test_chapters_section_route.py::test_truly_empty_section_still_404` — for a hypothetical empty `references` section (find one in the corpus or mock it), assert 404 still emitted.
- `test_chapters_section_route.py::test_unknown_chapter_returns_chapter_not_found_404` — `get_chapter_section("NBK9999999", summary)` → 404 with `code=chapter_not_found` (not `section_empty_for_chapter`).

**Effort:** S. One conditional + 1 model field + 1 helper call + 4 tests.

---

### B12 — `get_fulltext` "fuzzy" section matching is substring-only

**Root cause.** [`api/routes/fulltext.py:44-62`](../../genereview_link/api/routes/fulltext.py) `_filter_sections()` does `key.lower() in tokens or any(tok in key.lower() for tok in tokens)`. Pure substring. The docs claim "fuzzy".

**Solution.** Add a small alias map for common abbreviations, then use `rapidfuzz` for the residual case. `rapidfuzz>=3.6.0` is already a dependency (used in `services/gene_index.py`).

```python
# fulltext.py - _filter_sections (REV 2: per-token fuzzy fallback)
from rapidfuzz import process, fuzz

_SECTION_ALIASES: dict[str, str] = {
    "mgmt": "management",
    "tx": "management",
    "rx": "management",
    "dx": "diagnosis",
    "diag": "diagnosis",
    "cf": "clinical_features",
    "molgen": "molecular_genetics",
    "counseling": "genetic_counseling",
    "refs": "references",
}

def _filter_sections(all_sections: dict, requested: str | None) -> dict:
    if not requested:
        return all_sections
    tokens = [t.strip().lower() for t in requested.split(",") if t.strip()]
    matched: dict = {}
    for token in tokens:
        # Track whether THIS token found any match — per-token, not global.
        token_matched = False

        # Step 1: exact alias match
        canonical = _SECTION_ALIASES.get(token, token)
        if canonical in all_sections:
            matched[canonical] = all_sections[canonical]
            token_matched = True
            continue

        # Step 2: substring match (existing behavior, preserved)
        for key in all_sections:
            if token in key.lower():
                matched[key] = all_sections[key]
                token_matched = True

        # Step 3: fuzzy fallback ONLY if THIS token still has no match
        if not token_matched:
            result = process.extractOne(
                token, list(all_sections.keys()), scorer=fuzz.ratio, score_cutoff=70,
            )
            if result:
                matched[result[0]] = all_sections[result[0]]
    return matched
```

**Key correction vs rev 1.** The rev-1 sketch's `if not matched:` was global — once *any* token matched, all subsequent unmatched tokens skipped fuzzy fallback. The rev-2 version uses a per-token `token_matched` flag so each token's miss is independently rescued by fuzzy match. Also de-duplicated the duplicate `"mgmt"` key in the alias dict.

**Tests.**
- `sections="mgmt"` → matches `management` (alias path).
- `sections="diagnosi"` (typo) → matches `diagnosis` via fuzz fallback.
- **`sections="management,diagnosi"`** → returns *both* `management` (alias miss / direct match) AND `diagnosis` (per-token fuzzy fallback). This is the regression case that rev-1 would have broken.
- `sections="completely_unrelated_word"` → returns empty dict.
- `sections="mgmt,clinical_features"` → both match (alias + exact key).

**Effort:** S. Helper rewrite + 5 tests + doc update.

---

### B13 — `nbk_id` not zero-pad-normalized

**Root cause.** Pattern `^NBK\d+$` at [`api/routes/chapters.py:63`](../../genereview_link/api/routes/chapters.py) and elsewhere. Repository uses direct `nbk_id = $1` parameterized query at [`retrieval/repository.py:319-331`](../../genereview_link/retrieval/repository.py). PostgreSQL does not consider `'NBK0001247'` equal to `'NBK1247'`.

**Solution.** Add boundary-layer normalization in a single helper used by all route handlers and the repository:

```python
# Add to genereview_link/models/sections.py (or a new normalize.py)
import re

_NBK_PATTERN = re.compile(r"^NBK0*(\d+)$")

def canonicalize_nbk_id(raw: str) -> str:
    """Strip leading zeros from the numeric portion. NBK0001247 -> NBK1247.

    Returns the input unchanged if it does not match the pattern. Callers should
    still validate against ^NBK\\d+$ separately.
    """
    m = _NBK_PATTERN.fullmatch(raw)
    return f"NBK{m.group(1)}" if m else raw
```

Then call `nbk_id = canonicalize_nbk_id(nbk_id)` at the top of every route handler that accepts `nbk_id`. This is the smallest-surface fix; alternative is to normalize inside the repository, but boundary-layer is cleaner.

**Tests.**
- `canonicalize_nbk_id("NBK0001247") == "NBK1247"`
- `canonicalize_nbk_id("NBK1247") == "NBK1247"`
- `canonicalize_nbk_id("NBK0") == "NBK0"` (preserves trailing single zero)
- Integration: `get_chapter_metadata(nbk_id="NBK0001247")` → 200 (was 404).

**Effort:** XS. Helper + 4 callsites + 4 tests.

---

## Consensus improvements (from cross-LLM synthesis)

### C1 — `get_license` tool wrapper (consensus 3/5) (REV 2)

**Current state.** The `/license` REST route already exists at [`api/routes/license.py`](../../genereview_link/api/routes/license.py). However, [`server_manager.py:444-445`](../../genereview_link/server_manager.py) **explicitly excludes it from MCP tool exposure**:

```python
# Exclude /license from MCP tools — served as genereview://license resource instead
RouteMap(pattern=r"^/license$", mcp_type=MCPType.EXCLUDE),
```

So this is a policy reversal, not a greenfield addition. **REV 2 chooses path (a) — flip the policy** because 3 of 5 independent LLM reviewers reached for `get_license` as a tool name, indicating the resource-only policy creates a real discoverability tax. The resource stays in place; the tool is added alongside it for callers that prefer tools.

**Solution.**

**C1a — Remove the MCP exclusion** at [`server_manager.py:444-445`](../../genereview_link/server_manager.py). The existing route description and response model carry over automatically.

**C1b — Ensure the existing `/license` route has a usable MCP description.** Inspect the route handler and add a leading sentence per the [Anthropic guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) ("explain when to use it"):

```python
# api/routes/license.py — strengthen the docstring
@router.get("/license", response_model=LicenseData, operation_id="get_license")
async def get_license() -> LicenseData:
    """Get attribution and citation terms for the GeneReviews corpus.

    Use this tool when emitting a citation block, compiling a research-use
    disclosure, or verifying redistribution terms before exporting passages.
    Returns the same content as the genereview://license resource (resources
    remain available for tool-aware consumers; this tool is for parity with
    callers that prefer tools).
    """
    return _LICENSE_DATA  # constant; Unicode (©, —) pre-decoded to literals
```

**C1c — Update or remove the existing exclusion test** at `tests/test_mcp_tool_dispatch.py` (or wherever the exclusion is asserted). The test currently asserts `/license` is NOT exposed as a tool; flip it to assert it IS exposed.

**C1d — Decode Unicode in the response payload.** The resource currently returns the JSON with `©` (©) and `—` (—) escapes (verified during the deep review). For the tool path, pre-decode to literal characters in `_LICENSE_DATA` so an LLM consumer doesn't have to JSON-decode again.

**Tests.**
- New: assert `/license` is listed in the MCP tool registry (counterpart to the existing exclusion test).
- New: call the MCP tool path and assert the `©` and `—` characters are literal, not escape sequences.
- Updated: any test asserting the resource still resolves continues to pass (we keep `genereview://license` registered).

**Effort:** XS. Remove one RouteMap entry + strengthen one docstring + update Unicode + flip 1 exclusion test + add 1 tool-path test.

---

### C2 — Lead tool descriptions with their highest-leverage affordance (consensus discoverability gap)

**Solution.** Rewrite the opening sentence of every tool description so the highest-value parameter or behavior leads. Per Anthropic's [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) guidance, "make implicit context explicit" and "avoid ambiguity by clearly describing expected inputs."

Concrete copy edits to ship as part of Batch C3 (or alongside the future ranking-architecture-redesign spec when it lands — whichever ships first):

**`search_passages` — current first line:**
> Returns ranked passages from the active GeneReviews corpus.

**Proposed:**
> Returns ranked passages from the active GeneReviews corpus. **For intervention/treatment queries, always pass `sections=["management"]`; for diagnostic-criteria queries, pass `sections=["diagnosis","clinical_features"]`. This is the single biggest precision lever.** Use `mode="brief"` (default) for triage and `mode="full"` to skip a follow-up `get_passage` call.

**`get_chapter_metadata` — current first line:**
> Return chapter title, last-updated date, gene symbols, section counts, and table count.

**Proposed:**
> **The chapter outline tool.** Returns chapter title, dates, gene symbols, per-section passage_count, AND the full `tables[]` list with each table's `table_id`, caption, section, and heading_path. Always call this before `get_chapter_section` or `get_table` to avoid guessing.

**`get_chapter_section` — add to first line:**
> ... Use `include=["concatenated_text"]` for the joined-text view (overlap stripped by default since Batch C1). Add `?dedupe=false` only when you need the literal per-chunk text.

(All descriptions are also documented in `genereview://usage`; the change is to move the high-value affordances into the per-tool description for first-time-consumer discoverability.)

**Effort:** XS. Description copy edits in 6 routes.

---

### C3 — Rank-disagreement diagnostic suggestion (consensus 3/5) — DEFERRED

Moved to the separate ranking-architecture-redesign spec, because the diagnostic is only meaningful once independent dense and lexical ranks are both computed corpus-wide (today's gated pipeline can't surface a useful disagreement signal — the dense rank is constrained to the lexical-top-50 candidates).

---

## Architectural notes from research

### MCP tool design (Anthropic guidance)
- Tool descriptions should be treated with prompt-engineering rigor — iterate based on consumer error patterns ([Anthropic engineering blog](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- Avoid generic list tools; prefer targeted search/filter.
- Surface meaningful identifiers (e.g., `passage_id` `NBK1247:0024`) over UUIDs.
- Use `response_format` enum patterns (which is exactly what `mode=brief/full/ids_only` does — keep this pattern).

### Hybrid retrieval (PostgreSQL + dense)
- The current `to_tsquery` + dense + RRF stack is the consensus best-of-breed for hybrid retrieval in 2025 ([Tiger Data: From ts_rank to BM25](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres), [ParadeDB: Hybrid Search in PostgreSQL](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)) — **but only when dense and lexical run independently corpus-wide**. The current implementation gates dense behind lexical-top-50, which is what the ranking-architecture-redesign spec (separate doc) will fix.
- `pg_textsearch` (true BM25) is an optional upgrade from `ts_rank` for stronger lexical precision; consider for the ranking-redesign spec.
- Cross-encoder rerank (BGE-reranker-v2-m3, ms-marco-MiniLM-L12) is the next step beyond RRF; also belongs in the ranking-redesign spec.

### Pydantic + FastMCP
- FastMCP auto-generates output schema from return type annotations ([gofastmcp.com/servers/tools](https://gofastmcp.com/servers/tools)).
- For conditional response shapes (the B1 issue), the recommended pattern is `Union[FullRow, SlimRow]` with a discriminator field — adopted in rev 2 for B1 because `RankedPassage` requires 7 fields that the slim shape cannot supply.

### NCBI Bookshelf NXML semantics
- The `<pub-history>` element with `<date date-type="updated">` is the authoritative editorial date. The codebase's parser is correct ([NCBI Bookshelf FTP](https://www.ncbi.nlm.nih.gov/books/NBK554846/), [Frequently Asked Questions](https://www.ncbi.nlm.nih.gov/books/NBK45610/)).
- For chapters whose NXML lacks `updated`, the existing fallback to `<date date-type="revised">` is correct.

### PubMed EFetch (book articles)
- GeneReviews PMIDs use the `PubmedBookArticle` shape; the title lives in `<BookTitle>` and structured abstracts use `<AbstractText Label="...">`. The `pubmed_parser` library ([titipata/pubmed_parser](https://github.com/titipata/pubmed_parser)) is the reference implementation.

---

## Migration order and rollout

### Batch C1 — Ship-blockers + parser fixes (1 sprint)
1. B1 — `mode=ids_only` (S, 1 day)
2. B11 — summary 404 → 200 (XS, 0.5 day)
3. B13 — NBK ID canonicalization (XS, 0.5 day)
4. B9 — dedupe default flip (XS, 0.5 day)
5. B6 — table rowspan parser (M, 2-3 days + reingest)
6. B10 — `_meta.attribution` on E-utils models (XS, 0.5 day)

**Gate:** `make ci-local` + smoke against fresh `NBK1247` Table 4 showing 4 cells per row.

### Batch C2 — Ranking quality + freshness (1-2 sprints)
1. B5 — `chapter_ingested_at` exposure + corpus reingest of stale chapters (M, ~3 days incl. ingest run)
2. B8 — lexical multi-token safety net (CTE + recall_terms coverage filter) (S, 1 day)
3. C2 (description copy) — lead-with-affordance rewrites for `search_passages`, `get_chapter_metadata`, `get_chapter_section` (XS, 0.5 day)

**Gate:** `make ci-local` clean + smoke confirming `chapter_ingested_at` surfaces on metadata + lexical-mode regression on the 5-token BRCA query no longer returns Fukuyama CMD.

B7 (cross-reference at rank 1) is **not** in this batch — it is handled in the separate ranking-architecture-redesign spec.

### Batch C3 — Legacy E-utils + low-severity ergonomics (1 sprint)
1. B12 — fuzzy section matching (S, 1 day)
2. C1 — `get_license` tool wrapper (XS, 0.5 day)
3. B2 — `get_abstract` parser (M, 2 days)
4. B3 — `get_links` parser (M, 2 days)
5. B4 — `get_fulltext` deduplication (M, 2 days)

**Gate:** all four E-utils tools return well-formed, non-duplicated responses on PMID 20301425 + NBK1247.

### Total estimate
~3 sprints of focused work (~6 weeks at 2 dev-days/issue average), or compressed to ~3 weeks if parallelized across two engineers (one on C1+C2, one on C3).

---

## Tests budget

Estimated new tests:
- **C1**: ~15 new tests (ids_only validation, summary section 200, NBK canonicalization, rowspan parser cases, dedupe default, attribution presence).
- **C2**: ~6 new tests (`chapter_ingested_at` presence, freshness diagnostic on aged rows, lexical CTE coverage filter, multi-token regression).
- **C3**: ~10 new tests (fuzzy matching, license tool, abstract parser, links parser, fulltext dedup).

Plus regression fixture refresh: `tests/fixtures/nxml/table_with_rowspan.nxml`, `tests/fixtures/efetch/NBK1247_book_article.xml`, `tests/fixtures/elink/PMID20301425_*.xml`, `tests/fixtures/bookshelf_html/NBK1247_management.html`.

---

## Out of scope / deferred (Batch D)

- HGVS-aware tokenization (preserve `c.NNN`, `p.X`, `g.X` as single tokens). Worth doing for variant queries but requires custom dictionary registration in PostgreSQL.
- Cross-encoder rerank option (`rerank=ce` or `rerank=hybrid_ce`). Defer until Batch C2 ships and we measure the rule-based classifier's coverage.
- `mode=brief` on `get_chapter_section` (consensus item #5 from R1/R2). Low priority once B9 (dedupe default) is in.
- `related_passage_ids` field resolving in-text cross-references to passage IDs. Belongs in the ranking-architecture-redesign spec alongside B7.
- BM25 (`pg_textsearch`) replacement for `ts_rank`. Plumbing change with uncertain payoff vs current 3-tsquery scheme.

---

## Research and code-mapping methodology

This spec was produced from:

- **4 parallel `Explore` subagents** (read-only) mapping the bug-relevant code paths. Agent outputs are summarized inline; each `file:line` reference traces back to one of these agents.
- **8 parallel WebSearch + WebFetch queries** for external best practices:
  - Cross-encoder rerank for biomedical RAG (BGE, MS MARCO, BioASQ 2025)
  - Anthropic MCP tool description best practices (`/engineering/writing-tools-for-agents`)
  - NCBI Bookshelf NXML `pub-history` semantics
  - Passage role classification / cross-reference detection
  - PostgreSQL tsquery + HGVS nomenclature + BM25 hybrid retrieval
  - FastMCP discriminated union output validation
  - PubMed EFetch structured AbstractText parsing
  - Anthropic tool description prefix / when-not-to-use guidance

- **Inline code reads** of `genereview_link/api/routes/search.py`, `genereview_link/api/routes/passages.py` (sample), and the `tests/fixtures/` directory.

All findings cross-referenced with the deep review at `docs/superpowers/reviews/2026-05-12-mcp-llm-deep-toolset-review.md`. Bug IDs B1–B13 map 1:1 between the two documents.

---

## Sources

- [Writing effective tools for AI agents — Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Code execution with MCP — Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [FastMCP — Tools documentation](https://gofastmcp.com/servers/tools)
- [Beyond Retrieval: Ensembling Cross-Encoders and GPT Rerankers with LLMs for Biomedical QA (arXiv:2507.05577)](https://arxiv.org/html/2507.05577v1)
- [BAAI/bge-reranker-v2-m3 — Hugging Face](https://huggingface.co/BAAI/bge-reranker-base)
- [The Best Rerankers — comprehensive evaluation framework](https://medium.com/@markshipman4273/the-best-rerankers-24d9582c3495)
- [Hybrid Search in PostgreSQL: The Missing Manual — ParadeDB](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [From ts_rank to BM25: pg_textsearch — Tiger Data](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres)
- [HGVS Nomenclature](https://hgvs-nomenclature.org/stable/)
- [PostgreSQL Text Search Types — Documentation](https://www.postgresql.org/docs/current/datatype-textsearch.html)
- [NCBI Bookshelf FAQ](https://www.ncbi.nlm.nih.gov/books/NBK45610/)
- [titipata/pubmed_parser — GitHub](https://github.com/titipata/pubmed_parser)
- [Rule-based methods for text classification in NLP](https://blog.pangeanic.com/rule-based-methods-for-text-classification-in-nlp)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/specification/2025-11-25)
