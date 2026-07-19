# Group A API Reliability Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Group A issues (#41, #42, #35, #47) by making orchestration entry points repository-first where possible, structurally recoverable on failure, and consistent in corpus-version reporting.

**Architecture:** Add narrow helpers for repository resolution, response version stamping, and structured orchestration errors. Reuse existing `GeneReviewRepository.get_chapter_by_gene()` / `get_chapter_by_pmid()` and keep live NCBI behavior for `fresh=true` and fallback. Update route descriptions and `genereview://usage` after behavior is implemented.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, asyncpg-backed repository, existing `EutilsClient`, pytest, Ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-13-group-a-api-reliability-design.md`

---

## File Map

**Modify:**
- `genereview_link/api/routes/search.py` — repository-first `search_genereviews`, structured failures, version stamping.
- `genereview_link/api/routes/genereview.py` — pass `Request`, support repository-aware service call, structured failures, version stamping.
- `genereview_link/api/routes/abstract.py` — version stamping, structured failures, route description.
- `genereview_link/api/routes/fulltext.py` — version stamping, structured failures, route description.
- `genereview_link/api/routes/links.py` — version stamping, structured failures, route description.
- `genereview_link/services/genereview_service.py` — optional repository-first comprehensive lookup and live fallback switch.
- `genereview_link/services/service_manager.py` — inject repository into `GeneReviewService` when app state has one, or keep route-level service construction if cleaner.
- `genereview_link/api/eutils_client.py` — fix live PubMed-to-Bookshelf resolver.
- `genereview_link/api/resources/usage.py` — document orchestration fallbacks and structured errors.
- `genereview_link/mcp/prompts.py` — align prompt wording if needed.

**Create:**
- `genereview_link/api/orchestration.py` — shared helpers for optional repository access and response version stamping.
- `genereview_link/api/orchestration_errors.py` — structured error builders for orchestration routes.

**Tests:**
- `tests/test_routes_with_mocks.py` — extend existing orchestration route tests.
- `tests/test_eutils_client_mocked.py` — add resolver parsing regression.
- `tests/test_api_errors.py` — add orchestration error helper assertions.
- `tests/test_tool_schema_descriptions.py` — assert route descriptions mention fallbacks.
- `tests/test_mcp_license_resource.py` or new usage-resource test if needed — assert usage text contains canonical fallback wording.

---

### Task 1: Add Shared Orchestration Helpers

**Files:**
- Create: `genereview_link/api/orchestration.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Create helper module**

Add:

```python
"""Shared helpers for legacy orchestration routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from fastapi import Request

from genereview_link.models.genereview_models import ResponseMeta
from genereview_link.retrieval.repository import GeneReviewRepository


class VersionedResponse(Protocol):
    corpus_version: str | None
    meta: ResponseMeta


def get_optional_repository(request: Request) -> GeneReviewRepository | None:
    repo = getattr(request.app.state, "repository", None)
    return repo


def active_corpus_version(request: Request) -> str | None:
    return getattr(request.app.state, "corpus_version", None)


def live_corpus_version() -> str:
    return f"live:{datetime.now(UTC).isoformat()}"


def stamp_response_version(
    response: VersionedResponse,
    *,
    corpus_version: str | None,
) -> None:
    response.corpus_version = corpus_version
    response.meta.corpus_version = corpus_version
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
make test-fast
```

Expected: existing tests still pass or fail only because later tasks have not
yet wired the helper.

- [ ] **Step 3: Commit**

```bash
git add genereview_link/api/orchestration.py
git commit -m "feat(api): add orchestration response helpers"
```

---

### Task 2: Add Structured Orchestration Error Builders

**Files:**
- Create: `genereview_link/api/orchestration_errors.py`
- Test: `tests/test_api_errors.py`

- [ ] **Step 1: Write helper tests**

Add tests that call the builders and assert:

```python
from genereview_link.api.orchestration_errors import (
    gene_not_found_error,
    pmid_resolver_failed_error,
)


def test_gene_not_found_error_has_search_passages_fallback() -> None:
    err = gene_not_found_error("BRCA1")
    detail = err.detail
    assert detail["code"] == "gene_not_found"
    assert detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "search_passages"
    assert detail["next_commands"][0]["arguments"]["gene"] == "BRCA1"


def test_pmid_resolver_failed_error_echoes_pmid() -> None:
    err = pmid_resolver_failed_error("20301425", gene_symbol="BRCA1")
    assert err.detail["code"] == "pmid_resolver_failed"
    assert "20301425" in err.detail["message"]
```

- [ ] **Step 2: Implement builders**

Add:

```python
"""Structured errors for orchestration entry points."""

from __future__ import annotations

from typing import Any

from genereview_link.api.errors import StructuredHTTPException


def _search_passages_command(gene_symbol: str) -> dict[str, Any]:
    gene = gene_symbol.upper()
    return {"tool": "search_passages", "arguments": {"gene": gene, "q": gene}}


def gene_not_found_error(gene_symbol: str) -> StructuredHTTPException:
    gene = gene_symbol.upper()
    return StructuredHTTPException(
        status_code=404,
        code="gene_not_found",
        message=f"No GeneReviews chapter was found for gene symbol {gene}.",
        recovery_hint=(
            "Try search_passages with the gene filter, or broaden the query if "
            "the gene may be mentioned in a multi-gene chapter."
        ),
        next_commands=[_search_passages_command(gene)],
    )


def pmid_resolver_failed_error(
    pubmed_id: str,
    *,
    gene_symbol: str | None = None,
) -> StructuredHTTPException:
    commands: list[dict[str, Any]] = []
    if gene_symbol:
        commands.append(_search_passages_command(gene_symbol))
    return StructuredHTTPException(
        status_code=502,
        code="pmid_resolver_failed",
        message=f"Could not resolve PubMed ID {pubmed_id} to an NCBI Bookshelf chapter.",
        recovery_hint=(
            "Use corpus-backed passage search when possible; live PubMed links "
            "can omit Bookshelf relationships even for indexed GeneReviews."
        ),
        next_commands=commands,
    )


def upstream_ncbi_unavailable_error(action: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=502,
        code="upstream_ncbi_unavailable",
        message=f"NCBI was unavailable while attempting to {action}.",
        recovery_hint="Retry later or use indexed corpus tools such as search_passages.",
    )


def internal_orchestration_error(action: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=500,
        code="internal_error",
        message=f"An internal error occurred while attempting to {action}.",
        recovery_hint=(
            "Retry once. If the error persists, use search_passages or "
            "get_chapter_metadata for indexed corpus retrieval."
        ),
    )
```

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_api_errors.py -q
```

Expected: new tests pass.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/orchestration_errors.py tests/test_api_errors.py
git commit -m "feat(api): add structured orchestration errors"
```

---

### Task 3: Fix Live PubMed-to-Bookshelf Resolver

**Files:**
- Modify: `genereview_link/api/eutils_client.py`
- Test: `tests/test_eutils_client_mocked.py`

- [ ] **Step 1: Add resolver regression test**

Add a mocked `_make_request()` test:

```python
@pytest.mark.asyncio
async def test_get_book_url_from_pmid_accepts_pubmed_books_linkset(monkeypatch):
    client = EutilsClient()

    async def fake_request(endpoint, params):
        assert endpoint == "elink.fcgi"
        assert params["dbfrom"] == "pubmed"
        assert params["id"] == "20301425"
        assert params["retmode"] == "json"
        assert "cmd" not in params
        return {
            "linksets": [
                {
                    "linksetdbs": [
                        {"dbto": "pubmed_books", "links": ["1247"]},
                    ]
                }
            ]
        }

    monkeypatch.setattr(client, "_make_request", fake_request)
    assert await client.get_book_url_from_pmid("20301425") == (
        "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
    )
```

- [ ] **Step 2: Update implementation**

Change `get_book_url_from_pmid()` so params omit `cmd=prlinks` and DB matching is:

```python
dbto = str(db.get("dbto", "")).lower()
if "book" not in dbto:
    continue
links = db.get("links", [])
if not links:
    continue
book_id = str(links[0])
if book_id.upper().startswith("NBK"):
    return f"https://www.ncbi.nlm.nih.gov/books/{book_id.upper()}/"
return f"https://www.ncbi.nlm.nih.gov/books/NBK{book_id}/"
```

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_eutils_client_mocked.py -q
```

Expected: resolver test passes.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/test_eutils_client_mocked.py
git commit -m "fix(api): resolve PubMed Bookshelf links from book linksets"
```

---

### Task 4: Make `search_genereviews` Repository-First

**Files:**
- Modify: `genereview_link/api/routes/search.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add route tests**

Add tests with a fake repository:

```python
class FakeChapter:
    nbk_id = "NBK1247"
    short_name = "brca1"
    title = "BRCA1- and BRCA2-Associated HBOC"
    pubmed_id = "20301425"
    gene_symbols = ("BRCA1", "BRCA2")


class FakeRepo:
    async def get_chapter_by_gene(self, gene_symbol: str):
        assert gene_symbol == "BRCA1"
        return FakeChapter()


async def test_search_genereviews_uses_repository_first(async_client, fastapi_app):
    fastapi_app.state.repository = FakeRepo()
    fastapi_app.state.corpus_version = "2026-05-10-r6"
    resp = await async_client.get("/search/BRCA1")
    body = resp.json()
    assert resp.status_code == 200
    assert body["ids"] == ["20301425"]
    assert body["corpus_version"] == "2026-05-10-r6"
    assert body["_meta"]["corpus_version"] == "2026-05-10-r6"
```

- [ ] **Step 2: Implement route behavior**

In `search_genereviews()`:

```python
repo = get_optional_repository(request)
version = live_corpus_version() if fresh else active_corpus_version(request)
if repo is not None and not fresh:
    chapter = await repo.get_chapter_by_gene(gene_symbol.upper())
    if chapter is not None and chapter.pubmed_id:
        out = SearchResult(
            count=1,
            retmax=retmax,
            retstart=0,
            ids=[chapter.pubmed_id],
            webenv="",
            querykey="",
        )
        stamp_response_version(out, corpus_version=version)
        return out
```

Keep live fallback for `fresh=true` and no local hit. Convert unexpected
exceptions to `internal_orchestration_error("search GeneReviews")`.

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_routes_with_mocks.py -q
```

Expected: repository-first and existing fresh tests pass after expected test
updates for `_meta.corpus_version`.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/routes/search.py tests/test_routes_with_mocks.py
git commit -m "fix(api): search GeneReviews from corpus before live NCBI"
```

---

### Task 5: Make `get_genereview_summary` Repository-First

**Files:**
- Modify: `genereview_link/services/genereview_service.py`
- Modify: `genereview_link/api/routes/genereview.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add route/service test**

Add a fake service or fake repo test asserting the route returns NBK URL from
local chapter and does not call `get_book_url_from_pmid()`.

Expected JSON assertions:

```python
assert body["gene_symbol"] == "BRCA1"
assert body["pubmed_id"] == "20301425"
assert body["book_url"] == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
assert body["corpus_version"] == "2026-05-10-r6"
```

- [ ] **Step 2: Extend service signature**

Change comprehensive method wrapper and implementation to accept:

```python
async def _get_genereview_comprehensive_impl(
    self,
    gene_symbol: str,
    include_abstract: bool = True,
    include_links: bool = True,
    include_fulltext: bool = True,
    *,
    repository: GeneReviewRepository | None = None,
    fresh: bool = False,
) -> GeneReview:
```

Because `alru_cache` should not cache repository objects, do not route this
new signature through the cached wrapper. Add a public uncached method:

```python
async def get_genereview_comprehensive_uncached(
    self,
    gene_symbol: str,
    include_abstract: bool = True,
    include_links: bool = True,
    include_fulltext: bool = True,
    *,
    repository: GeneReviewRepository | None = None,
    fresh: bool = False,
) -> GeneReview:
    return await self._get_genereview_comprehensive_impl(
        gene_symbol,
        include_abstract=include_abstract,
        include_links=include_links,
        include_fulltext=include_fulltext,
        repository=repository,
        fresh=fresh,
    )
```

- [ ] **Step 3: Implement repository-first resolution**

At the top of `_get_genereview_comprehensive_impl()`:

```python
chapter = None
if repository is not None and not fresh:
    chapter = await repository.get_chapter_by_gene(gene_symbol.upper())

if chapter is not None and chapter.pubmed_id:
    pubmed_id = chapter.pubmed_id
    book_url = f"https://www.ncbi.nlm.nih.gov/books/{chapter.nbk_id}/"
    title = chapter.title
else:
    # existing live search + resolver path
```

Avoid calling `get_book_url_from_pmid()` in the local branch.

- [ ] **Step 4: Update route**

Make route accept `request: Request`, resolve optional repo, call uncached
method with `repository=repo, fresh=fresh`, stamp version, and convert
`DataNotFoundError` to `gene_not_found_error(gene_symbol)`.

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_routes_with_mocks.py -q
```

Expected: `get_genereview_summary` tests pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/services/genereview_service.py genereview_link/api/routes/genereview.py tests/test_routes_with_mocks.py
git commit -m "fix(api): build GeneReview summaries from indexed chapters"
```

---

### Task 6: Stamp Corpus Version On Abstract, Links, And Fulltext

**Files:**
- Modify: `genereview_link/api/routes/abstract.py`
- Modify: `genereview_link/api/routes/links.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add tests**

For each endpoint, set `app.state.corpus_version = "2026-05-10-r6"` and assert:

```python
assert body["corpus_version"] == "2026-05-10-r6"
assert body["_meta"]["corpus_version"] == "2026-05-10-r6"
```

Keep existing `fresh=true` assertions but add `_meta`:

```python
assert body["corpus_version"].startswith("live:")
assert body["_meta"]["corpus_version"] == body["corpus_version"]
```

- [ ] **Step 2: Update each route**

Add `request: Request` and replace local version stamping with:

```python
version = live_corpus_version() if fresh else active_corpus_version(request)
stamp_response_version(out, corpus_version=version)
```

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_routes_with_mocks.py -q
```

Expected: fresh and non-fresh corpus-version tests pass.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/routes/abstract.py genereview_link/api/routes/links.py genereview_link/api/routes/fulltext.py tests/test_routes_with_mocks.py
git commit -m "fix(api): stamp corpus version on orchestration responses"
```

---

### Task 7: Convert Orchestration Route Failures To Structured Errors

**Files:**
- Modify: `genereview_link/api/routes/search.py`
- Modify: `genereview_link/api/routes/genereview.py`
- Modify: `genereview_link/api/routes/abstract.py`
- Modify: `genereview_link/api/routes/links.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Test: `tests/test_routes_with_mocks.py`

- [ ] **Step 1: Add error shape tests**

For broken mocked dependencies, assert each route error has:

```python
detail = body["detail"]
assert detail["code"]
assert detail["recovery_hint"]
assert "next_commands" in detail
```

For `search_genereviews` and `get_genereview_summary`, assert first fallback
tool is `search_passages`.

- [ ] **Step 2: Replace bare `HTTPException` paths**

Use the helpers from Task 2:

```python
except DataNotFoundError as e:
    raise gene_not_found_error(gene_symbol) from e
except StructuredHTTPException:
    raise
except Exception as e:
    logger.exception("Unexpected orchestration failure", exc_info=True)
    raise internal_orchestration_error("fetch GeneReview summary") from e
```

For live upstream failures from client calls, use
`upstream_ncbi_unavailable_error("fetch abstract")`.

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_routes_with_mocks.py tests/test_api_errors.py -q
```

Expected: structured error tests pass.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/routes/search.py genereview_link/api/routes/genereview.py genereview_link/api/routes/abstract.py genereview_link/api/routes/links.py genereview_link/api/routes/fulltext.py tests/test_routes_with_mocks.py
git commit -m "fix(api): return structured errors from orchestration routes"
```

---

### Task 8: Update Route Descriptions And Usage Resource

**Files:**
- Modify: `genereview_link/api/routes/search.py`
- Modify: `genereview_link/api/routes/genereview.py`
- Modify: `genereview_link/api/routes/abstract.py`
- Modify: `genereview_link/api/routes/links.py`
- Modify: `genereview_link/api/routes/fulltext.py`
- Modify: `genereview_link/api/resources/usage.py`
- Modify: `genereview_link/mcp/prompts.py` if needed
- Test: `tests/test_tool_schema_descriptions.py`

- [ ] **Step 1: Add description tests**

Assert generated route schema or module route descriptions contain:

```python
assert "search_passages" in search_description
assert "fresh=true" in summary_description
assert "corpus_version" in abstract_description
assert "structured" in usage_markdown.lower()
assert "pmid_resolver_failed" in usage_markdown
```

- [ ] **Step 2: Update route descriptions**

Use concise text:

```text
Search the indexed GeneReviews corpus by gene symbol before live NCBI. If no
indexed chapter is found, retry with `search_passages(gene=<symbol>)` for
passage-level retrieval. Pass `fresh=true` to bypass the corpus and call live
NCBI.
```

Apply equivalent wording to summary, abstract, links, and fulltext.

- [ ] **Step 3: Update `genereview://usage`**

Add a section:

```markdown
## Orchestration Entry Points

`search_genereviews` and `get_genereview_summary` are convenience tools. They
resolve gene -> PubMed -> NBK and can fail when a resolver or upstream NCBI link
is unavailable. On `pmid_resolver_failed` or `gene_not_found`, prefer
`search_passages(gene=<symbol>, q=<symbol>)` to retrieve indexed chapter
evidence directly.

Default corpus-backed responses carry `_meta.corpus_version`. `fresh=true`
bypasses indexed context and returns `live:<timestamp>`.
```

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest tests/test_tool_schema_descriptions.py -q
```

Expected: description tests pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/search.py genereview_link/api/routes/genereview.py genereview_link/api/routes/abstract.py genereview_link/api/routes/links.py genereview_link/api/routes/fulltext.py genereview_link/api/resources/usage.py genereview_link/mcp/prompts.py tests/test_tool_schema_descriptions.py
git commit -m "docs(api): document orchestration fallbacks"
```

---

### Task 9: Verification Gate

**Files:**
- No code changes unless verification exposes a real issue.

- [ ] **Step 1: Run local CI**

```bash
make ci-local
```

Expected: formatting, linting, type checking, and tests pass.

- [ ] **Step 2: Inspect git state**

```bash
git status --short --branch
```

Expected: only intentional commits are present; unrelated pre-existing files
remain untouched.

- [ ] **Step 3: Summarize issue coverage**

Confirm:

- #41: repository-first summary/search and live resolver fallback fixed.
- #42: orchestration route failures are structured.
- #35: default and `fresh=true` corpus-version stamping is consistent.
- #47: route descriptions and usage docs document fallbacks.

- [ ] **Step 4: Commit any verification-only fixes**

If `make ci-local` required small fixes:

Stage the files reported by `git status --short` that were changed by this
Group A verification fix, then commit them:

```bash
git add genereview_link tests
git commit -m "test(api): verify group A reliability fixes"
```
