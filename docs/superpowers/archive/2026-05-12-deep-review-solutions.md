# Deep Review Solutions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the concrete MCP consumer defects found in the 2026-05-12 deep review, excluding B7 and rank-disagreement diagnostics which are deferred to a separate ranking architecture redesign.

**Architecture:** Three independently gated batches. Batch C1 fixes schema contracts, route consistency, table parsing, and attribution. Batch C2 exposes corpus ingest freshness and tightens lexical recall coverage. Batch C3 repairs the legacy E-utils and Bookshelf scraper path, adds the license tool wrapper, and improves first-time tool descriptions.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, Pydantic v2, asyncpg, PostgreSQL full-text search, defusedxml, BeautifulSoup/lxml, rapidfuzz, pytest, Ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-12-deep-review-solutions-design.md`

**Review:** `docs/superpowers/reviews/2026-05-12-mcp-llm-deep-toolset-review.md`

---

## File Map

**Modify:**
- `genereview_link/models/genereview_models.py` - add `IdsOnlyPassage`, `IdsOnlySearchResponse`, `LinkEntry`, `_meta` on live passthrough models, `chapter_ingested_at`, and `ChapterSectionResponse.note`.
- `genereview_link/models/sections.py` - add `canonicalize_nbk_id()`.
- `genereview_link/api/routes/passages.py` - typed `ids_only` response, NBK normalization, stale-ingest diagnostic, lexical description copy.
- `genereview_link/api/routes/chapters.py` - NBK normalization, default `dedupe=true`, empty summary section 200 response, tool description copy, `chapter_ingested_at`.
- `genereview_link/api/routes/tables.py` - NBK normalization.
- `genereview_link/api/routes/fulltext.py` - NBK normalization, per-token fuzzy section matching, live passthrough `_meta`.
- `genereview_link/api/routes/abstract.py` - live passthrough `_meta`.
- `genereview_link/api/routes/links.py` - categorized links, live passthrough `_meta`.
- `genereview_link/api/routes/license.py` - route description and literal license punctuation.
- `genereview_link/api/resources/usage.py` - dedupe default note and lexical-mode warning.
- `genereview_link/api/eutils_client.py` - PubmedBookArticle abstract parsing, categorized elink parsing, non-duplicating Bookshelf section extraction.
- `genereview_link/corpus/tables.py` - rowspan/colspan row parser.
- `genereview_link/retrieval/repository.py` - `ingested_at` projections and lexical coverage CTE.
- `genereview_link/server_manager.py` - expose `/license` as an MCP tool and include `get_license` in custom names.

**Create:**
- `tests/fixtures/nxml/table_with_rowspan.nxml`
- `tests/fixtures/efetch/NBK1247_book_article.xml`
- `tests/fixtures/elink/PMID20301425_prlinks.xml`
- `tests/fixtures/elink/PMID20301425_llinks.xml`
- `tests/fixtures/elink/PMID20301425_neighbor.xml`
- `tests/fixtures/bookshelf_html/NBK1247_management.html`

**Extend tests:**
- `tests/test_mcp_search_passages_params.py`
- `tests/test_routes_passages.py`
- `tests/test_response_envelope_models.py`
- `tests/test_chapters_section_route.py`
- `tests/test_routes_chapter_metadata.py`
- `tests/test_routes_table.py`
- `tests/unit/test_corpus_tables.py`
- `tests/test_routes_with_mocks.py`
- `tests/test_eutils_client_mocked.py`
- `tests/test_scraper_parsers.py`
- `tests/test_scraper_integration.py`
- `tests/test_mcp_tool_dispatch.py`
- `tests/test_license_route.py`
- `tests/test_mcp_license_resource.py`
- `tests/test_mcp_usage_resource.py`

## Batch C1 - Ship Blockers And Parser Contracts

### Task 1: Fix `search_passages(mode="ids_only")` Schema

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/passages.py`
- Test: `tests/test_mcp_search_passages_params.py`
- Test: `tests/test_routes_passages.py`
- Test: `tests/test_response_envelope_models.py`

- [ ] **Step 1: Add failing model and route tests**

Add these tests before implementation:

```python
def test_ids_only_response_model_uses_slim_rows() -> None:
    response = IdsOnlySearchResponse(
        results=[
            IdsOnlyPassage(
                passage_id="NBK1247:0024",
                nbk_id="NBK1247",
                chapter_section="management",
                rrf_score=0.1,
                lexical_rank_position=2,
            )
        ]
    )

    dumped = response.model_dump(by_alias=True)

    assert set(dumped["results"][0]) == {
        "passage_id",
        "nbk_id",
        "chapter_section",
        "rrf_score",
        "lexical_rank_position",
    }
    assert "_meta" in dumped
```

Update the existing ids-only route test to assert every result has exactly the same five row keys and `_meta` is present.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py::test_ids_only_response_model_uses_slim_rows tests/test_routes_passages.py::test_search_passages_ids_only -q
```

Expected: fail because `IdsOnlyPassage` and `IdsOnlySearchResponse` do not exist or the route still returns raw `JSONResponse` rows without `nbk_id`.

- [ ] **Step 3: Add slim models**

Add to `genereview_link/models/genereview_models.py` near `RankedPassage`:

```python
class IdsOnlyPassage(BaseModel):
    """Lean row shape for search_passages(mode='ids_only')."""

    passage_id: str
    nbk_id: str
    chapter_section: SectionName
    rrf_score: float | None = None
    lexical_rank_position: int | None = None


class IdsOnlySearchResponse(BaseModel):
    """Envelope returned by GET /passages/search when mode=ids_only."""

    results: list[IdsOnlyPassage]
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)
    model_config = {"populate_by_name": True}
```

- [ ] **Step 4: Return the typed slim response**

In `genereview_link/api/routes/passages.py`, import the new models and change the route decorator and signature:

```python
from genereview_link.models.genereview_models import (
    IdsOnlyPassage,
    IdsOnlySearchResponse,
    ...
)

@router.get(
    "/passages/search",
    response_model=PassageSearchResponse | IdsOnlySearchResponse,
    response_model_by_alias=True,
    ...
)
async def search_passages(...) -> PassageSearchResponse | IdsOnlySearchResponse | JSONResponse:
```

Replace the `mode == "ids_only"` branch with:

```python
if mode == "ids_only":
    meta = ResponseMeta(corpus_version=corpus, diagnostics=diagnostics_model)
    return IdsOnlySearchResponse(
        results=[
            IdsOnlyPassage(
                passage_id=r.passage.passage_id,
                nbk_id=r.passage.nbk_id,
                chapter_section=cast(SectionName, r.passage.chapter_section),
                rrf_score=r.rrf_score,
                lexical_rank_position=r.lexical_rank_position,
            )
            for r in ranked
        ],
        meta=meta,
    )
```

Keep `passage_role` out of the ids-only row; the advertised contract is the five fields above.

- [ ] **Step 5: Assert the FastMCP schema**

Add a test to `tests/test_mcp_search_passages_params.py` that builds the MCP app as existing tests do, reads the `search_passages` output schema, and asserts the ids-only branch does not require `chapter_title`, `char_count`, `recommended_citation`, or `source_url`.

Run:

```bash
uv run pytest tests/test_mcp_search_passages_params.py tests/test_routes_passages.py::test_search_passages_ids_only tests/test_response_envelope_models.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/passages.py tests/test_mcp_search_passages_params.py tests/test_routes_passages.py tests/test_response_envelope_models.py
git commit -m "fix: type ids-only passage search response"
```

### Task 2: Normalize NBK IDs At Route Boundaries

**Files:**
- Modify: `genereview_link/models/sections.py`
- Modify: `genereview_link/api/routes/passages.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Modify: `genereview_link/api/routes/tables.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_section_enum.py`
- Test: `tests/test_routes_chapter_metadata.py`
- Test: `tests/test_routes_table.py`
- Test: `tests/test_routes_passages.py`

- [ ] **Step 1: Write failing normalizer tests**

Add to `tests/test_section_enum.py`:

```python
from genereview_link.models.sections import canonicalize_nbk_id


def test_canonicalize_nbk_id_strips_leading_zeroes() -> None:
    assert canonicalize_nbk_id("NBK0001247") == "NBK1247"
    assert canonicalize_nbk_id("NBK1247") == "NBK1247"
    assert canonicalize_nbk_id("NBK0") == "NBK0"
    assert canonicalize_nbk_id("ABC") == "ABC"
```

Add route tests using `NBK0001247` for chapter metadata, table fetch, and search `nbk_id` filter.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_section_enum.py::test_canonicalize_nbk_id_strips_leading_zeroes tests/test_routes_chapter_metadata.py tests/test_routes_table.py tests/test_routes_passages.py -q
```

Expected: the new normalizer import fails and zero-padded route calls return 404 or empty results.

- [ ] **Step 3: Add the helper**

Add to `genereview_link/models/sections.py`:

```python
import re

_NBK_PATTERN = re.compile(r"^NBK0*(\d+)$")


def canonicalize_nbk_id(raw: str) -> str:
    """Strip leading zeroes from the numeric portion of an NBK ID."""
    match = _NBK_PATTERN.fullmatch(raw)
    if match is None:
        return raw
    return f"NBK{match.group(1)}"
```

- [ ] **Step 4: Apply it in route handlers**

Import and call `canonicalize_nbk_id()` at the start of every route that accepts a standalone NBK ID:

```python
from genereview_link.models.sections import canonicalize_nbk_id

nbk_id = canonicalize_nbk_id(nbk_id)
```

Apply this in:
- `search_passages()` before `applied_filters` and repository calls.
- `get_chapter_section()` before `repo.get_section()`.
- `get_chapter_metadata()` before `repo.get_chapter_metadata()`.
- `get_table()` before `repo.get_table()`.
- `get_fulltext()` before deriving `clean_id`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_section_enum.py tests/test_routes_chapter_metadata.py tests/test_routes_table.py tests/test_routes_passages.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/sections.py genereview_link/api/routes/passages.py genereview_link/api/routes/chapters.py genereview_link/api/routes/tables.py genereview_link/api/routes/fulltext.py tests/test_section_enum.py tests/test_routes_chapter_metadata.py tests/test_routes_table.py tests/test_routes_passages.py
git commit -m "fix: canonicalize zero-padded NBK ids"
```

### Task 3: Return 200 For Systematically Unscraped Summary Sections

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Test: `tests/test_chapters_section_route.py`
- Test: `tests/test_response_envelope_models.py`

- [ ] **Step 1: Write failing tests**

Add model assertion:

```python
def test_chapter_section_response_has_note_field() -> None:
    response = ChapterSectionResponse(
        nbk_id="NBK1247",
        chapter_title="BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        chapter_section="summary",
        passages=[],
        passage_count=0,
        note="Summary is not currently indexed; use the NCBI Bookshelf chapter.",
    )

    assert response.note is not None
```

Add route assertions:
- `GET /chapters/NBK1247/sections/summary` returns 200.
- `passages == []`.
- `passage_count == 0`.
- `note` contains `https://www.ncbi.nlm.nih.gov/books/NBK1247/`.
- `GET /chapters/NBK9999999/sections/summary` returns 404 with `code == "chapter_not_found"`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py::test_chapter_section_response_has_note_field tests/test_chapters_section_route.py -q
```

Expected: fail because `ChapterSectionResponse.note` does not exist and summary still raises `section_empty_for_chapter`.

- [ ] **Step 3: Add the response field**

Add to `ChapterSectionResponse`:

```python
note: str | None = None
```

- [ ] **Step 4: Return an empty section response for known systematic gaps**

In `genereview_link/api/routes/chapters.py`, import:

```python
from genereview_link.models.sections import SYSTEMATICALLY_UNSCRAPED_SECTIONS
from genereview_link.retrieval.repository import _note_for_empty_section
```

Replace the empty-passages branch with:

```python
if not passages:
    chapter = await repo.get_chapter_by_nbk(nbk_id)
    if chapter is None:
        raise StructuredHTTPException(
            status_code=404,
            code="chapter_not_found",
            message=f"chapter {nbk_id!r} not in corpus",
            recovery_hint="check the NBK ID; use search_passages to discover indexed chapters",
            next_commands=[
                {"tool": "search_passages", "arguments": {"q": "<gene symbol or term>"}}
            ],
        )
    if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
        return ChapterSectionResponse(
            nbk_id=nbk_id,
            chapter_title=chapter.title,
            chapter_section=section,
            chapter_last_updated=chapter.last_updated_date,
            passages=[],
            passage_count=0,
            note=_note_for_empty_section(section, nbk_id),
            meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
        )
    raise StructuredHTTPException(
        status_code=404,
        code="section_empty_for_chapter",
        message=f"chapter {nbk_id!r} has no passages in section {section!r}",
        recovery_hint=(
            "the chapter exists but this section has no rows. Use search_passages "
            "with nbk_id=<chapter> to discover which sections this chapter actually "
            "populates, or try a different section."
        ),
        next_commands=[
            {"tool": "search_passages", "arguments": {"q": "<your query>", "nbk_id": nbk_id}}
        ],
    )
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py tests/test_chapters_section_route.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/chapters.py tests/test_response_envelope_models.py tests/test_chapters_section_route.py
git commit -m "fix: return noted empty summary sections"
```

### Task 4: Flip `get_chapter_section` Dedupe Default

**Files:**
- Modify: `genereview_link/api/routes/chapters.py`
- Modify: `genereview_link/api/resources/usage.py`
- Test: `tests/test_chapters_section_route.py`
- Test: `tests/test_mcp_usage_resource.py`

- [ ] **Step 1: Update tests first**

Replace the default-overlap assertion with:

```python
def test_dedupe_true_default_strips_overlap(client) -> None:
    response = client.get(
        "/chapters/NBK1247/sections/management",
        params={"include": "concatenated_text"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["concatenated_char_count"] == len(data["concatenated_text"])
    assert data["concatenated_char_count"] < sum(len(p["text"]) for p in data["passages"])
```

Add an opt-out test:

```python
def test_dedupe_false_preserves_literal_chunk_text(client) -> None:
    response = client.get(
        "/chapters/NBK1247/sections/management",
        params={"include": "concatenated_text", "dedupe": "false"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["concatenated_text"] == "\n\n".join(p["text"] for p in data["passages"])
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_chapters_section_route.py::test_dedupe_true_default_strips_overlap tests/test_chapters_section_route.py::test_dedupe_false_preserves_literal_chunk_text -q
```

Expected: default test fails because `dedupe=False` is still the default.

- [ ] **Step 3: Change the default and description**

In `get_chapter_section()`:

```python
dedupe: Annotated[
    bool,
    Query(
        description=(
            "Strip overlapping text between adjacent chunks "
            "(longest-common-suffix/prefix heuristic). "
            "Default True for LLM-ready joined text. Pass false only when "
            "you need literal stored chunk text."
        ),
    ),
] = True,
```

Update the docstring first line to mention `include=concatenated_text` strips overlap by default.

- [ ] **Step 4: Update usage resource**

In `genereview_link/api/resources/usage.py`, add this sentence to the `get_chapter_section` section:

```markdown
`include=["concatenated_text"]` returns joined text with chunk overlap stripped by default. Pass `dedupe=false` only for corpus-auditing workflows that need literal stored chunk text.
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_chapters_section_route.py tests/test_mcp_usage_resource.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/routes/chapters.py genereview_link/api/resources/usage.py tests/test_chapters_section_route.py tests/test_mcp_usage_resource.py
git commit -m "fix: dedupe concatenated section text by default"
```

### Task 5: Parse NXML Tables With Rowspan And Colspan

**Files:**
- Modify: `genereview_link/corpus/tables.py`
- Create: `tests/fixtures/nxml/table_with_rowspan.nxml`
- Test: `tests/unit/test_corpus_tables.py`
- Test: `tests/test_routes_table.py`

- [ ] **Step 1: Add the fixture**

Create `tests/fixtures/nxml/table_with_rowspan.nxml`:

```xml
<table-wrap id="T.rowspan">
  <caption><title>Rowspan sample</title></caption>
  <table>
    <thead>
      <tr><th>Condition</th><th>Action</th><th>Frequency</th></tr>
    </thead>
    <tbody>
      <tr><td rowspan="4">Breast cancer</td><td>Self-exam</td><td>Monthly</td></tr>
      <tr><td>Clinical exam</td><td>Every 6-12 months</td></tr>
      <tr><td>Mammogram</td><td>Annually</td></tr>
      <tr><td>MRI</td><td>Annually</td></tr>
      <tr><td>Ovarian cancer</td><td colspan="2">No effective screening</td></tr>
      <tr><td>A</td><th>B</th><td>C</td></tr>
    </tbody>
  </table>
</table-wrap>
```

- [ ] **Step 2: Add failing unit tests**

Add tests:

```python
def test_rowspan_propagates_only_declared_rows() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[:4] == [
        ["Breast cancer", "Self-exam", "Monthly"],
        ["Breast cancer", "Clinical exam", "Every 6-12 months"],
        ["Breast cancer", "Mammogram", "Annually"],
        ["Breast cancer", "MRI", "Annually"],
    ]
    assert table.rows[4][0] == "Ovarian cancer"


def test_colspan_expands_cells() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[4] == ["Ovarian cancer", "No effective screening", "No effective screening"]


def test_mixed_th_td_preserves_source_order() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[5] == ["A", "B", "C"]
```

Use the existing XML fixture loader style in `tests/unit/test_corpus_tables.py`.

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
uv run pytest tests/unit/test_corpus_tables.py -q
```

Expected: rowspan and colspan tests fail.

- [ ] **Step 4: Implement `parse_rows()`**

Add this helper to `genereview_link/corpus/tables.py`:

```python
def _positive_int_attr(node: Any, name: str) -> int:
    raw = node.get(name, "1")
    try:
        return max(int(raw or "1"), 1)
    except ValueError:
        return 1


def parse_rows(table_elem: Any) -> list[list[str]]:
    """Parse NXML table rows, expanding rowspan and colspan."""
    rows: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}

    for tr in table_elem.findall(".//tr"):
        row: list[str] = []
        col_idx = 0
        cells = iter(child for child in tr if child.tag in {"td", "th"})

        while True:
            while col_idx in pending:
                value, remaining = pending[col_idx]
                row.append(value)
                if remaining > 1:
                    pending[col_idx] = (value, remaining - 1)
                else:
                    del pending[col_idx]
                col_idx += 1

            cell = next(cells, None)
            if cell is None:
                break

            value = _text_or_empty(cell)
            colspan = _positive_int_attr(cell, "colspan")
            rowspan = _positive_int_attr(cell, "rowspan")

            for _ in range(colspan):
                row.append(value)
                if rowspan > 1:
                    pending[col_idx] = (value, rowspan - 1)
                col_idx += 1

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

Then replace tbody row extraction with:

```python
rows = parse_rows(tbody)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_corpus_tables.py tests/test_routes_table.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/corpus/tables.py tests/fixtures/nxml/table_with_rowspan.nxml tests/unit/test_corpus_tables.py tests/test_routes_table.py
git commit -m "fix: preserve table rowspans and colspans"
```

### Task 6: Add `_meta.attribution` To Live Passthrough Models

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/abstract.py`
- Modify: `genereview_link/api/routes/links.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_response_envelope_models.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add failing model tests**

Add:

```python
def test_live_passthrough_meta_uses_underscore_alias() -> None:
    abstract = AbstractData(
        pmid="20301425",
        title="BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        abstract="GeneReviews abstract",
        journal="GeneReviews",
        publication_date="1998",
    )

    dumped = abstract.model_dump(by_alias=True)

    assert "_meta" in dumped
    assert "meta" not in dumped
    assert dumped["_meta"]["corpus_version"] is None
    assert dumped["_meta"]["attribution"]
```

Add equivalent assertions for `LinkData` and `FullTextData`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py::test_live_passthrough_meta_uses_underscore_alias -q
```

Expected: fail because the live models do not expose `_meta`.

- [ ] **Step 3: Add `ResponseMeta.live_passthrough()` and model fields**

Add to `ResponseMeta`:

```python
@classmethod
def live_passthrough(cls) -> "ResponseMeta":
    """Metadata for live NCBI passthrough responses not tied to an indexed corpus."""
    return cls(corpus_version=None)
```

Add to `AbstractData`, `LinkData`, and `FullTextData`:

```python
meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta.live_passthrough)
model_config = {"populate_by_name": True}
```

- [ ] **Step 4: Keep routes returning model instances**

Verify `abstract.py`, `links.py`, and `fulltext.py` instantiate the Pydantic models rather than raw dicts. Where a route returns a dict from the client, wrap it:

```python
return AbstractData(**payload)
```

For `LinkData`, preserve `corpus_version` and allow the model default to add `_meta`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py tests/test_routes_with_mocks.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/abstract.py genereview_link/api/routes/links.py genereview_link/api/routes/fulltext.py tests/test_response_envelope_models.py tests/test_routes_with_mocks.py
git commit -m "fix: add attribution meta to live passthrough tools"
```

## Batch C2 - Freshness And Lexical Safety

### Task 7: Expose `chapter_ingested_at` And Stale Corpus Diagnostics

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/retrieval/repository.py`
- Modify: `genereview_link/api/routes/passages.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Test: `tests/test_routes_chapter_metadata.py`
- Test: `tests/test_routes_passages.py`
- Test: `tests/integration/test_repository_metadata.py`

- [ ] **Step 1: Add failing assertions**

Add metadata route assertion:

```python
def test_chapter_metadata_includes_ingested_at(client) -> None:
    response = client.get("/chapters/NBK1247/metadata")
    assert response.status_code == 200
    assert response.json()["chapter_ingested_at"] is not None
```

Add search assertion by mocking ranked rows with `chapter_ingested_at` older than 180 days and asserting `_meta.diagnostics.suggestions` contains `"corpus-may-be-stale"`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_routes_chapter_metadata.py::test_chapter_metadata_includes_ingested_at tests/test_routes_passages.py -q
```

Expected: fail because `chapter_ingested_at` is not projected.

- [ ] **Step 3: Add fields to rows and models**

Update dataclasses:

```python
class ChapterRow:
    ...
    ingested_at: datetime | None = None


class PassageRow:
    ...
    chapter_ingested_at: datetime | None = None


class ChapterMetadataRow:
    ...
    chapter_ingested_at: datetime | None = None
```

Update Pydantic models:

```python
class RankedPassage(BaseModel):
    ...
    chapter_ingested_at: datetime | None = None


class ChapterMetadataResponse(BaseModel):
    ...
    chapter_ingested_at: datetime | None = None
```

- [ ] **Step 4: Project `ingested_at` from SQL**

In every passage-select query that joins `genereview_chapters c`, include:

```sql
c.ingested_at as chapter_ingested_at
```

In chapter queries include:

```sql
select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
       authors, initial_pub_date, last_updated_date, ingested_at
```

In metadata include:

```sql
select nbk_id, title, last_updated_date, ingested_at, gene_symbols
```

Update `_to_chapter_row()` and `_row_to_passage()` to populate the new fields.

- [ ] **Step 5: Emit the stale diagnostic**

In `search_passages()` after `diagnostics_model` is built:

```python
from datetime import UTC, datetime, timedelta

ingest_dates = [r.passage.chapter_ingested_at for r in ranked[:3] if r.passage.chapter_ingested_at]
if ingest_dates and datetime.now(UTC) - min(ingest_dates) > timedelta(days=180):
    diagnostics_model.suggestions.append("corpus-may-be-stale")
```

When constructing `RankedPassage`, pass:

```python
chapter_ingested_at=r.passage.chapter_ingested_at,
```

When constructing `ChapterMetadataResponse`, pass:

```python
chapter_ingested_at=meta.chapter_ingested_at,
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/test_routes_chapter_metadata.py tests/test_routes_passages.py tests/integration/test_repository_metadata.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/retrieval/repository.py genereview_link/api/routes/passages.py genereview_link/api/routes/chapters.py tests/test_routes_chapter_metadata.py tests/test_routes_passages.py tests/integration/test_repository_metadata.py
git commit -m "feat: expose chapter ingest freshness"
```

### Task 8: Tighten Lexical Coverage For Multi-Token Queries

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Modify: `genereview_link/api/routes/passages.py`
- Modify: `genereview_link/api/resources/usage.py`
- Test: `tests/test_routes_passages.py`
- Test: `tests/integration/test_repository_lexical.py`

- [ ] **Step 1: Add failing lexical regression tests**

Add:

```python
async def test_lexical_variant_query_with_context_keeps_brca_hits(client) -> None:
    response = client.get(
        "/passages/search",
        params={
            "q": "c.5266dupC BRCA1 founder variant Ashkenazi",
            "rerank": "lexical",
            "limit": 5,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert any(row["nbk_id"] == "NBK1247" for row in data["results"])
```

Add a repository integration test that asserts every returned lexical row for a five-token query has `recall_overlap_count >= 2`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_routes_passages.py::test_lexical_variant_query_with_context_keeps_brca_hits tests/integration/test_repository_lexical.py -q
```

Expected: the BRCA assertion fails or the integration test shows one-term low-coverage rows.

- [ ] **Step 3: Restructure SQL into CTEs**

In `GeneReviewRepository.search_passages()`, replace the `cand` and `ranked` CTEs with a structure where `recall_overlap_count` and `recall_terms_count` are produced before the coverage filter:

```sql
with q as (
    select
        phraseto_tsquery('english', $2) as phrase_query,
        websearch_to_tsquery('english', $2) as strict_query,
        to_tsquery('english', $7) as recall_query,
        $8::text[] as recall_terms
),
scored as (
    select
        p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
        p.section_level, p.chunk_index, p.text,
        c.gene_symbols,
        c.title as chapter_title,
        c.last_updated_date as chapter_last_updated,
        c.ingested_at as chapter_ingested_at,
        p.passage_type, p.passage_role, p.table_id, p.table_data,
        ts_rank_cd(p.search_vector, q.phrase_query) as phrase_rank,
        ts_rank_cd(p.search_vector, q.strict_query) as strict_rank,
        ts_rank_cd(p.search_vector, q.recall_query) as recall_rank,
        (
            select count(*)
              from unnest(q.recall_terms) as term
             where p.search_vector @@ plainto_tsquery('english', term)
        )::int as recall_overlap_count,
        cardinality(q.recall_terms)::int as recall_terms_count
      from genereview_passages p
      join genereview_chapters c on c.nbk_id = p.nbk_id, q
     where (
              p.search_vector @@ q.phrase_query
           or p.search_vector @@ q.strict_query
           or p.search_vector @@ q.recall_query
          )
       and ($3::text is null or $3 = any(c.gene_symbols))
       and ($4::text is null or p.nbk_id = $4)
       and ($5::text[] is null or p.chapter_section = any($5::text[]))
       and ($9::text is null or p.heading_path ILIKE '%' || $9 || '%')
),
ranked as (
    select
        *,
        (phrase_rank * 3.0 + strict_rank * 2.0 + recall_rank)
        * case
            when phrase_rank = 0 and strict_rank = 0 and recall_rank > 0
              and recall_terms_count >= 4
              and recall_overlap_count <= 2
            then least(1.0, greatest(0.25, char_length(text)::double precision / 400.0))
            else 1.0
          end as lexical_rank
      from scored
     where recall_overlap_count >= greatest(1, ceiling(0.25 * recall_terms_count)::int)
     order by lexical_rank desc, nbk_id, passage_id
     limit $6
)
select ranked.*{snippet_select}
  from ranked, q
```

Keep the existing Python `terms = recall_terms(query)` as `$8`.

- [ ] **Step 4: Update descriptions**

In `passages.py`, update the `rerank` Query description for `lexical`:

```python
'"lexical" (weighted lexical score with section-priority tiebreaker - best for exact gene-symbol or variant strings; for multi-token clinical concept queries, use "rrf")'
```

In `usage.py`, add:

```markdown
For variant nomenclature queries in `rerank="lexical"`, prefer the variant token alone, for example `q="c.5266dupC"`. Adding broad context words such as "founder" or "variant" can still widen recall; use default `rerank="rrf"` for multi-token clinical questions.
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_routes_passages.py tests/integration/test_repository_lexical.py tests/test_mcp_usage_resource.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/retrieval/repository.py genereview_link/api/routes/passages.py genereview_link/api/resources/usage.py tests/test_routes_passages.py tests/integration/test_repository_lexical.py tests/test_mcp_usage_resource.py
git commit -m "fix: filter low-coverage lexical recall matches"
```

### Task 9: Lead Tool Descriptions With High-Leverage Affordances

**Files:**
- Modify: `genereview_link/api/routes/passages.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Modify: `genereview_link/api/routes/tables.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_tool_schema_descriptions.py`

- [ ] **Step 1: Add description assertions**

Update `tests/test_tool_schema_descriptions.py` so the OpenAPI/MCP description for:
- `search_passages` contains `sections=["management"]`.
- `get_chapter_metadata` starts with `The chapter outline tool`.
- `get_chapter_section` contains `overlap stripped by default`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_tool_schema_descriptions.py -q
```

Expected: fail until copy is updated.

- [ ] **Step 3: Update route descriptions**

Use these first sentences:

```python
"Returns ranked passages from the active GeneReviews corpus. For intervention/treatment queries, pass sections=[\"management\"]; for diagnostic-criteria queries, pass sections=[\"diagnosis\", \"clinical_features\"]. This is the biggest precision lever."
```

```python
summary="The chapter outline tool: title, dates, gene symbols, section counts, and tables"
```

```python
"""The chapter outline tool. Returns chapter title, dates, gene symbols, per-section passage_count, and the full tables[] list with table_id, caption, section, and heading_path."""
```

```python
"Fetch all passages for a section. Use include=concatenated_text for joined text with overlap stripped by default; pass dedupe=false only for literal chunk text."
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_tool_schema_descriptions.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/passages.py genereview_link/api/routes/chapters.py genereview_link/api/routes/tables.py genereview_link/api/routes/fulltext.py tests/test_tool_schema_descriptions.py
git commit -m "docs: lead mcp tool descriptions with affordances"
```

## Batch C3 - Legacy Path And Ergonomics

### Task 10: Improve Fulltext Section Fuzzy Matching

**Files:**
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_scraper_parsers.py`

- [ ] **Step 1: Add failing filter tests**

Add tests for `_filter_sections()`:

```python
def test_filter_sections_matches_alias() -> None:
    sections = {"management": section("Management"), "diagnosis": section("Diagnosis")}
    assert set(_filter_sections(sections, "mgmt")) == {"management"}


def test_filter_sections_uses_fuzzy_fallback_per_token() -> None:
    sections = {"management": section("Management"), "diagnosis": section("Diagnosis")}
    assert set(_filter_sections(sections, "management,diagnosi")) == {
        "management",
        "diagnosis",
    }


def test_filter_sections_unrelated_token_returns_empty() -> None:
    sections = {"management": section("Management")}
    assert _filter_sections(sections, "completely_unrelated_word") == {}
```

Use the existing helper style in `tests/test_scraper_parsers.py`.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_scraper_parsers.py -q
```

Expected: alias and typo tests fail.

- [ ] **Step 3: Implement alias plus per-token fuzzy fallback**

In `fulltext.py`:

```python
from rapidfuzz import fuzz, process

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
```

Replace `_filter_sections()` with:

```python
def _filter_sections(
    sections: dict[str, GeneReviewSection], requested: str | None
) -> dict[str, GeneReviewSection]:
    if not requested:
        return sections
    tokens = [tok.strip().lower() for tok in requested.split(",") if tok.strip()]
    if not tokens:
        return sections

    matched: dict[str, GeneReviewSection] = {}
    keys = list(sections)
    for token in tokens:
        token_matched = False
        canonical = _SECTION_ALIASES.get(token, token)
        if canonical in sections:
            matched[canonical] = sections[canonical]
            token_matched = True
            continue

        for key in keys:
            if token in key.lower():
                matched[key] = sections[key]
                token_matched = True

        if not token_matched:
            result = process.extractOne(token, keys, scorer=fuzz.ratio, score_cutoff=70)
            if result is not None:
                matched[result[0]] = sections[result[0]]

    return matched
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_scraper_parsers.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/fulltext.py tests/test_scraper_parsers.py
git commit -m "fix: improve fulltext section fuzzy matching"
```

### Task 11: Expose `/license` As MCP `get_license`

**Files:**
- Modify: `genereview_link/server_manager.py`
- Modify: `genereview_link/api/routes/license.py`
- Test: `tests/test_mcp_tool_dispatch.py`
- Test: `tests/test_license_route.py`
- Test: `tests/test_mcp_license_resource.py`

- [ ] **Step 1: Flip tests first**

Update the existing exclusion test so it asserts `get_license` is exposed in MCP tool names. Add:

```python
def test_license_payload_uses_literal_punctuation(client) -> None:
    response = client.get("/license")
    assert response.status_code == 200
    raw = response.text
    assert "\\u00a9" not in raw.lower()
    assert "\\u2014" not in raw.lower()
    assert "©" in raw
    assert "—" in raw
```

This file already contains canonical non-ASCII license text, so literal copyright and dash characters are intentional here.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_mcp_tool_dispatch.py tests/test_license_route.py tests/test_mcp_license_resource.py -q
```

Expected: MCP exposure test fails while `/license` is excluded.

- [ ] **Step 3: Remove the MCP route exclusion**

In `server_manager.py`, add custom name:

```python
"get_license": "get_license",
```

Remove:

```python
RouteMap(pattern=r"^/license$", mcp_type=MCPType.EXCLUDE),
```

- [ ] **Step 4: Strengthen the route docstring**

In `api/routes/license.py`:

```python
async def get_license() -> LicenseNotice:
    """Get attribution and citation terms for the GeneReviews corpus.

    Use this tool when emitting a citation block, compiling a research-use
    disclosure, or verifying redistribution terms before exporting passages.
    Returns the same content as the genereview://license resource.
    """
```

Ensure `_LICENSE_DATA` or the returned `LicenseNotice` uses literal `©` and `—` values from the existing model constants rather than escaped JSON strings.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_mcp_tool_dispatch.py tests/test_license_route.py tests/test_mcp_license_resource.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/server_manager.py genereview_link/api/routes/license.py tests/test_mcp_tool_dispatch.py tests/test_license_route.py tests/test_mcp_license_resource.py
git commit -m "feat: expose license as an mcp tool"
```

### Task 12: Parse PubmedBookArticle Titles And Structured Abstracts

**Files:**
- Modify: `genereview_link/api/eutils_client.py`
- Create: `tests/fixtures/efetch/NBK1247_book_article.xml`
- Test: `tests/test_eutils_client_mocked.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add fixture and failing parser test**

Create `tests/fixtures/efetch/NBK1247_book_article.xml` from PMID 20301425 EFetch XML. The fixture must contain a `PubmedBookArticle`, `BookDocument`, `BookTitle` or `ArticleTitle`, and multiple `AbstractText Label="..."` elements, including one with nested inline tags.

Add:

```python
def test_parse_book_article_preserves_title_and_labeled_abstract(load_xml) -> None:
    root = load_xml("efetch/NBK1247_book_article.xml")
    article = root.find(".//PubmedBookArticle")
    client = EutilsClient()

    parsed = client._parse_book_article(article, "20301425")

    assert parsed["title"]
    assert "DIAGNOSIS/TESTING:" in parsed["abstract"]
    assert "GENETIC COUNSELING:" in parsed["abstract"]
    assert not parsed["abstract"].endswith("of")
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_eutils_client_mocked.py::test_parse_book_article_preserves_title_and_labeled_abstract tests/test_routes_with_mocks.py -q
```

Expected: fails because `.text` truncates nested abstract content or title is empty for the fixture shape.

- [ ] **Step 3: Add `_itertext()` helper**

In `eutils_client.py`:

```python
def _itertext(elem: _StdET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())
```

- [ ] **Step 4: Update `_parse_book_article()`**

Replace title and abstract extraction with:

```python
title = (
    book_document.find(".//BookTitle")
    or book_document.find(".//ArticleTitle")
    or book_document.find(".//Book/BookTitle")
)
article_data["title"] = _itertext(title)

abstract_texts: list[str] = []
for abstract_text in book_document.findall(".//Abstract/AbstractText"):
    label = abstract_text.get("Label") or abstract_text.get("NlmCategory") or ""
    text = _itertext(abstract_text)
    if not text:
        continue
    if label and label.upper() != "UNLABELLED":
        abstract_texts.append(f"{label}: {text}")
    else:
        abstract_texts.append(text)
article_data["abstract"] = "\n\n".join(abstract_texts)
```

Use `_itertext()` for journal title too if a nested tag appears there.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_eutils_client_mocked.py tests/test_routes_with_mocks.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/fixtures/efetch/NBK1247_book_article.xml tests/test_eutils_client_mocked.py tests/test_routes_with_mocks.py
git commit -m "fix: parse pubmed book article abstracts"
```

### Task 13: Categorize ELink URLs Without Breaking `urls`

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/eutils_client.py`
- Modify: `genereview_link/api/routes/links.py`
- Create: `tests/fixtures/elink/PMID20301425_prlinks.xml`
- Create: `tests/fixtures/elink/PMID20301425_llinks.xml`
- Create: `tests/fixtures/elink/PMID20301425_neighbor.xml`
- Test: `tests/test_eutils_client_mocked.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add failing model and parser tests**

Add `LinkEntry` assertions:

```python
def test_link_data_keeps_flat_urls_and_adds_categorized_links() -> None:
    data = LinkData(
        urls=["https://www.ncbi.nlm.nih.gov/books/NBK1247/"],
        link_entries=[
            LinkEntry(
                url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                link_type="books",
                provider="NCBI Bookshelf",
            )
        ],
        by_type={"books": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]},
    )

    dumped = data.model_dump(mode="json", by_alias=True)
    assert dumped["urls"] == ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]
    assert dumped["link_entries"][0]["link_type"] == "books"
    assert dumped["by_type"]["books"]
```

Add parser tests for each fixture shape.

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py::test_link_data_keeps_flat_urls_and_adds_categorized_links tests/test_eutils_client_mocked.py -q
```

Expected: fail because `LinkEntry`, `link_entries`, and `by_type` do not exist.

- [ ] **Step 3: Add additive link models**

In `genereview_models.py`:

```python
class LinkEntry(BaseModel):
    url: str
    link_type: Literal["prlinks", "llinks", "books", "pmc"]
    provider: str | None = None


class LinkData(BaseModel):
    urls: list[str] = Field(default_factory=list, description="All available URLs for the publication.")
    link_entries: list[LinkEntry] | None = None
    by_type: dict[str, list[str]] = Field(default_factory=dict)
    corpus_version: str | None = None
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta.live_passthrough)
    model_config = {"populate_by_name": True}
```

- [ ] **Step 4: Parse standard elink shapes**

Change `EutilsClient.get_all_links()` to return all fields:

```python
async def get_all_links(self, pubmed_id: str) -> dict[str, Any]:
    params = {"dbfrom": "pubmed", "id": pubmed_id, "cmd": "prlinks"}
    root = await self._make_xml_request("elink.fcgi", params)
    entries = self._parse_link_entries(root)
    return {
        "urls": [entry["url"] for entry in entries],
        "link_entries": entries,
        "by_type": {
            link_type: [entry["url"] for entry in entries if entry["link_type"] == link_type]
            for link_type in sorted({entry["link_type"] for entry in entries})
        },
    }
```

Add:

```python
def _parse_link_entries(self, root: _StdET.Element) -> list[dict[str, str | None]]:
    entries: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()

    def add(url: str | None, link_type: str, provider: str | None = None) -> None:
        if not url:
            return
        key = (url, link_type)
        if key in seen:
            return
        seen.add(key)
        entries.append({"url": url, "link_type": link_type, "provider": provider})

    for obj_url in root.findall(".//IdUrlSet/ObjUrl"):
        add(obj_url.findtext("Url"), "prlinks", obj_url.findtext("Provider/Name"))
    for obj_url in root.findall(".//ObjUrl"):
        add(obj_url.findtext("Url"), "llinks", obj_url.findtext("Category"))
    for link_set_db in root.findall(".//LinkSetDb"):
        link_name = link_set_db.findtext("LinkName") or ""
        for link in link_set_db.findall("Link"):
            link_id = link.findtext("Id")
            if link_id and "books" in link_name.lower():
                add(f"https://www.ncbi.nlm.nih.gov/books/{link_id}/", "books", "NCBI Bookshelf")
            elif link_id and "pmc" in link_name.lower():
                add(f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{link_id}/", "pmc", "PubMed Central")
    return entries
```

- [ ] **Step 5: Return `LinkData` from route**

In `api/routes/links.py`, wrap the client payload:

```python
payload = await client.get_all_links(pmid)
return LinkData(**payload)
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/test_response_envelope_models.py tests/test_eutils_client_mocked.py tests/test_routes_with_mocks.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/eutils_client.py genereview_link/api/routes/links.py tests/fixtures/elink/PMID20301425_prlinks.xml tests/fixtures/elink/PMID20301425_llinks.xml tests/fixtures/elink/PMID20301425_neighbor.xml tests/test_response_envelope_models.py tests/test_eutils_client_mocked.py tests/test_routes_with_mocks.py
git commit -m "fix: categorize pubmed linkout urls"
```

### Task 14: Remove Duplicate Bookshelf Fulltext Emission

**Files:**
- Modify: `genereview_link/api/eutils_client.py`
- Create: `tests/fixtures/bookshelf_html/NBK1247_management.html`
- Test: `tests/test_scraper_parsers.py`
- Test: `tests/test_scraper_integration.py`

- [ ] **Step 1: Add fixture and failing duplicate test**

Create `tests/fixtures/bookshelf_html/NBK1247_management.html` from the live management section HTML used by the review.

Add:

```python
def test_hierarchical_sections_do_not_duplicate_management_paragraphs(load_html) -> None:
    soup = BeautifulSoup(load_html("bookshelf_html/NBK1247_management.html"), "lxml")
    client = EutilsClient()

    sections = client._extract_hierarchical_sections(soup)
    management = sections["management"]["content"]

    assert management.count("Consider prophylactic bilateral mastectomy") == 1
    assert set(sections["management"]) == {"title", "content", "level", "subsections"}
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_scraper_parsers.py::test_hierarchical_sections_do_not_duplicate_management_paragraphs -q
```

Expected: fails because content is emitted more than once.

- [ ] **Step 3: Add direct-child collection helpers**

In `eutils_client.py`:

```python
def _collect_direct_content(self, section_div: Tag, seen_nodes: set[int]) -> list[str]:
    blocks: list[str] = []
    for child in section_div.children:
        if not isinstance(child, Tag) or child.name is None:
            continue
        if id(child) in seen_nodes or child.name in {"h2", "h3"}:
            continue
        if child.name in {"p", "ul", "ol", "table"}:
            text = child.get_text(separator=" ", strip=True)
            if self._is_valid_content(text):
                blocks.append(text)
            seen_nodes.add(id(child))
    return blocks


def _collect_until_heading(self, heading: Tag, stop_tags: set[str], seen_nodes: set[int]) -> str:
    blocks: list[str] = []
    current = heading.find_next_sibling()
    while isinstance(current, Tag):
        if current.name in stop_tags:
            break
        if id(current) not in seen_nodes and current.name in {"p", "ul", "ol", "table"}:
            text = current.get_text(separator=" ", strip=True)
            if self._is_valid_content(text):
                blocks.append(text)
            seen_nodes.add(id(current))
        current = current.find_next_sibling()
    return self._clean_content(" ".join(blocks))
```

- [ ] **Step 4: Rewrite `_extract_hierarchical_sections()` structured path**

Inside the `if section_divs:` branch, replace `section_div.find_all(["p", "div", "ul", "ol"])` and the separate descendant h3 extraction with direct child iteration:

```python
seen_nodes: set[int] = set()
for section_div in section_divs:
    if id(section_div) in seen_nodes:
        continue
    h2_heading = section_div.find("h2", recursive=False) or section_div.find("h2")
    if not h2_heading:
        continue
    section_title = h2_heading.get_text().strip()
    if not section_title or len(section_title) < 3:
        continue

    section_content_parts = self._collect_direct_content(section_div, seen_nodes)
    subsections: dict[str, dict[str, Any]] = {}
    for child in section_div.children:
        if not isinstance(child, Tag) or child.name != "h3":
            continue
        subsection_title = child.get_text().strip()
        if not subsection_title or len(subsection_title) < 3:
            continue
        subsection_content = self._collect_until_heading(child, {"h2", "h3"}, seen_nodes)
        if subsection_content and len(subsection_content) > 30:
            subsection_key = self._normalize_section_key(subsection_title)
            subsections[subsection_key] = {
                "title": subsection_title,
                "content": subsection_content,
                "level": 3,
                "subsections": {},
            }
        seen_nodes.add(id(child))

    main_content = self._clean_content(" ".join(section_content_parts).strip())
    if main_content and len(main_content) > 50:
        section_key = self._normalize_section_key(section_title)
        sections[section_key] = {
            "title": section_title,
            "content": main_content,
            "level": 2,
            "subsections": subsections,
        }
        seen_nodes.add(id(section_div))
```

Do not call `.get_text()` on intermediate `div` containers.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_scraper_parsers.py tests/test_scraper_integration.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/fixtures/bookshelf_html/NBK1247_management.html tests/test_scraper_parsers.py tests/test_scraper_integration.py
git commit -m "fix: avoid duplicate bookshelf section text"
```

### Task 15: Batch Gate And Local CI

**Files:**
- Read: all modified files
- No code changes unless checks fail

- [ ] **Step 1: Run formatting**

Run:

```bash
make format
```

Expected: completes successfully and formats touched files.

- [ ] **Step 2: Run lint**

Run:

```bash
make lint
```

Expected: pass. Fix reported Ruff issues in the files touched by this plan only.

- [ ] **Step 3: Run type checking**

Run:

```bash
make typecheck
```

Expected: pass. Fix reported mypy issues in the files touched by this plan only.

- [ ] **Step 4: Run tests**

Run:

```bash
make test
```

Expected: pass.

- [ ] **Step 5: Run required completion check**

Run:

```bash
make ci-local
```

Expected: pass. This is required by `AGENTS.md` before claiming completion.

- [ ] **Step 6: Commit final verification fixes**

If Steps 1-5 required fixes, commit them:

```bash
git add genereview_link tests docs/superpowers
git commit -m "test: complete deep review solution gate"
```

If no fixes were required, do not create an empty commit.

## Self-Review Notes

- Spec coverage: B1, B2, B3, B4, B5, B6, B8, B9, B10, B11, B12, B13, C1, and C2 are covered. B7 and C3 are intentionally excluded because the design spec defers them to ranking architecture redesign.
- Placeholder scan: this plan contains no placeholder sections. Fixture capture paths are concrete and named.
- Type consistency: route response models use `meta: ResponseMeta = Field(alias="_meta", ...)`; ids-only search uses a distinct `IdsOnlySearchResponse`; live passthrough models keep `_meta` alias consistency.
- Backwards compatibility: `LinkData.urls` remains `list[str]`; `RankedPassage` keeps required citation fields for non-ids modes; `dedupe=false` keeps literal concatenation available.
