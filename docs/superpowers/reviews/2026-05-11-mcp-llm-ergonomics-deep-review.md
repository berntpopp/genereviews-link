---
pr: 11
branch: feat/mcp-llm-ergonomics
base: ed9a48d
head: 94976e2
reviewed: 2026-05-11
depth: deep
files_reviewed: 20
status: issues_found
findings:
  critical: 0
  important: 4
  minor: 6
  nit: 5
verdict: APPROVE WITH FOLLOW-UPS
---

# Deep review — PR #11 MCP LLM-Ergonomics Pass

> Historical record

**Scope.** 20 files, +1,289 / -82 LOC across 13 commits ed9a48d..94976e2.
Each phase already passed two-stage subagent reviews in isolation; this
review focuses on cross-cutting concerns those isolated reviews could not
catch.

**Method.** Read every changed source file plus the spec
(`2026-05-11-mcp-llm-ergonomics-design.md`) and plan
(`2026-05-11-mcp-llm-ergonomics.md`). Re-ran `make ci-local` (217 passed).
Probed the live container at `http://127.0.0.1:8765` for HTTP behaviour.
Probed `/mcp/` via JSON-RPC over Streamable-HTTP for `initialize`,
`tools/list`, `prompts/list`, and `tools/call` against `search_passages`
and `get_passage` (success + 404).

## Verdict

**APPROVE WITH FOLLOW-UPS.** The PR delivers on every documented goal of
the spec. Live verification confirms the new envelope, structured 404,
section enum, and `_meta.attribution` reach MCP clients correctly. The
only finding that gates production deployment is **IMP-01** (default
docker compose does not mount `/mcp/`), and that is a deployment-config
gap the PR notes had not flagged. None of the issues require redesign;
all are addressable in a small follow-up commit.

---

## Spec adherence at a glance

All 10 spec components are implemented and live-verified:

| # | Component | Status | Notes |
|---|---|---|---|
| 1 | Schema consistency (`nbk` → `nbk_id`) | DONE | `/passages/search?nbk_id=` and `/chapters/{nbk_id}/...` both wired; tests pin both renames |
| 2 | `get_passage(passage_id)` | DONE | Live: `GET /passages/NBK1247:0022` returns full envelope; structured 404 on unknown id; 422 on regex mismatch |
| 3 | Brief mode + projection + `limit=5` | DONE | Live: default response is 5 rows × ~300-char `**bold**` snippets, `text=null`; `exclude=` and `mode=full` both verified |
| 4 | Structured errors | DONE (intentionally narrow) | `passage_not_found` + `section_not_found` only — see IMP-04 |
| 5 | Server instructions + 1 prompt | DONE | `initialize` response carries `instructions` field with the canonical-pipeline blob; `find_in_section` discoverable via `prompts/list` |
| 6 | `chapter_title` / `chapter_last_updated` everywhere | DONE | All three call sites (search, get_section, get_passage) populate from joined `genereview_chapters` |
| 7 | `_meta.attribution` envelope | DONE | Live: both routes emit `_meta.attribution`; `_meta` (not `meta`) on the wire via `response_model_by_alias=True`; verified MCP `tools/call` returns the envelope intact in `structuredContent` |
| 8 | Rerank docs + per-param descriptions | DONE | OpenAPI dump shows non-empty `description` on every parameter; rerank-mode operational doc lives on the route description |
| 9 | Section enum (`SectionName` Literal) | DONE | OpenAPI emits `enum` for the path param, the `sections` query param, and every model field of type `SectionName`; `SECTION_NAMES` test-pinned against `SECTION_PRIORITY` |
| 10 | `corpus_version` on `_meta` | PARTIAL | Field exists but is **always `None`**; spec called for `(await repo.active_corpus_version()).version` — see IMP-02 |

---

## Strengths

1. **Cross-file invariants are tight where they matter most.** Every call
   site that constructs a `RankedPassage` (passages.py:172, debug.py:53)
   uses the same field set including the new `chapter_title`,
   `chapter_last_updated`, and the `mode`-aware `text|snippet` mutual
   exclusion. The `RankedPassage` docstring documents the contract; the
   route is the only place that decides which of `text`/`snippet` to
   populate. This is correct and consistent.
2. **The `_meta` alias drift risk is well-controlled.** Both
   `PassageSearchResponse` and `ChapterSectionResponse` use
   `Field(alias="_meta", ...)` + `populate_by_name=True`, both routes
   declare `response_model_by_alias=True`, and the OpenAPI schema
   correctly publishes the `_meta` key. Live MCP `tools/call` response
   confirms `_meta` reaches the client in both `content[].text` and
   `structuredContent`. The `exclude=` JSONResponse fast path also emits
   `_meta` directly — verified byte-for-byte identical to the typed path
   for the `attribution` field.
3. **The MCP tool surface is genuinely usable from a blank-context LLM.**
   `tools/list` shows nine tools with concrete descriptions; the
   `search_passages` description embeds the rerank-mode operational guide
   verbatim; `get_passage`'s `inputSchema` carries the `^NBK\d+:\d{4}$`
   pattern with a worked example; `sections` and `chapter_section` are
   exposed as JSONSchema `enum` arrays. The server-level `instructions`
   field is delivered to every client on `initialize` (verified — full
   blob with "Canonical pipeline" arrives in the handshake response).
4. **Test coverage actually exercises the new boundaries.** `test_mcp_tool_dispatch.py`
   includes a regression test that reuses `_build_app_with_state()` — the
   same fixture pattern that caught the earlier 503-via-MCP bug — for the
   new `get_passage` route. `test_section_enum.py::test_section_names_covers_section_priority_keys`
   pins the SECTION_NAMES↔SECTION_PRIORITY drift gate that was promised
   in the spec. `test_response_envelope_models.py::test_passage_search_response_meta_alias_is_underscore_meta`
   pins the `_meta` (not `meta`) alias.

---

## Important findings (4)

### IMP-01 — Default docker production stack does not mount `/mcp/`

**Files:**
- `/home/bernt-popp/development/genereviews-link/server.py:14-16`
- `/home/bernt-popp/development/genereviews-link/docker/Dockerfile:69`
- `/home/bernt-popp/development/genereviews-link/docker/docker-compose.yml:43`
- `/home/bernt-popp/development/genereviews-link/docker/docker-compose.prod.yml:27`

**Issue.** All of the new MCP-side ergonomics work
(`instructions=`, `find_in_section` prompt, the structured envelope
landing in `tools/call`) is only reachable when the server is started in
`unified` transport mode (`UnifiedServerManager.start_unified_server`
mounts `mcp_app` at `config.mcp_path`). But the production entrypoint
(`server.py`) only calls `manager.create_fastapi_app(config)` — it never
calls `start_unified_server`, never builds the FastMCP app, and never
mounts it. Both `docker-compose.yml` and `docker-compose.prod.yml` invoke
that entrypoint via gunicorn / uvicorn against `server:app`.

The only compose file in the tree that mounts `/mcp/` is
`docker-compose.override.gr-pg.yml`, which overrides the CMD to
`["genereview-link", "serve", "--transport", "unified", ...]`. That is
the override the user added during testing — not the canonical prod
config.

**Live verification.** The locally-running container on port 8765 was
started with the override and DOES serve `/mcp/`. The default `docker
compose -f docker-compose.yml -f docker-compose.prod.yml up` would NOT.

**Fix.**
- Add `app.mount("/mcp", ...)` wiring into `server.py`'s module-level
  `app` (mirroring `start_unified_server`), OR
- Switch `docker-compose.yml` (and the prod override) to
  `command: ["genereview-link", "serve", "--transport", "unified", ...]`,
  matching the gr-pg override, OR
- Switch the Dockerfile `CMD` to `genereview-link serve --transport unified`
  and let compose inherit it.

The CLI route is cleaner because it preserves the existing
`UnifiedServerManager` lifespan-chaining for the FastMCP HTTP session
manager (the comment at server_manager.py:399-413 explains why this is
load-bearing — building FastMCP against a separate "discovery" app
broke `app.state.repository` propagation in the past, and the
`test_unified_server_uses_single_app_instance` regression test pins it).
A `server.py`-side mount would have to replicate that lifespan
chain — risk-cheap, but easy to get wrong on refactor.

### IMP-02 — `_meta.corpus_version` is always `None`

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:206`

**Issue.** The spec (Component 7) called for `corpus_version` to be
populated from `(await repo.active_corpus_version()).version`, with the
note that "the active corpus version is cached at app-state level — one
DB call per process startup, invalidated by the existing release
watcher." The implementation has the field plumbed end-to-end (model,
schema, OpenAPI, both routes, live MCP envelope) but the value is
hardcoded:

```python
corpus: str | None = None  # later: await repo.active_corpus_version()
return PassageSearchResponse.model_validate({
    "results": out,
    "_meta": ResponseMeta(corpus_version=corpus),
})
```

The chapter section route (`chapters.py`) does not even set
`corpus_version` on its envelope — so `chapter_last_updated` carries the
publication date but the corpus snapshot version is silently absent
across both endpoints. An LLM consumer reading two results from
different corpus versions has no way to detect the mismatch.

**Fix.** Either:
1. Wire it up: cache `active_corpus_version().version` on
   `app.state.corpus_version` at lifespan startup, refresh on the release
   watcher tick, and read it in both routes. ~15 LOC.
2. Drop `corpus_version` from `ResponseMeta` until it can be wired
   honestly. The current "field present, value always null" state is
   worse than absent — it suggests data the server cannot actually
   provide.

I prefer option 1; it's small, the plumbing is already in place, and the
release-watcher cache invalidation hook already exists.

### IMP-03 — `LexicalPassageRow.gene_symbols` duplicates `LexicalPassageRow.passage.gene_symbols`

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/retrieval/repository.py:47,60`

**Issue.** The Task 1.2 review flagged this; it was not fixed in the
PR train. After Phase 1 added `gene_symbols` to `PassageRow` (line 47),
the pre-existing `gene_symbols` field on `LexicalPassageRow` (line 60)
became redundant. Both are populated from the same SQL column
(`c.gene_symbols`) at lines 227 and 234. Three sites already use
`r.passage.gene_symbols` while three others use `r.gene_symbols` — the
two are guaranteed identical only because they're hydrated from the same
row in the same query, but a future refactor that touches one and not
the other could quietly desync them.

**Fix.** Delete `LexicalPassageRow.gene_symbols`, route every consumer
through `r.passage.gene_symbols`. ~10 LOC change with mechanical test
updates. The PR notes already log this as a follow-up; please don't let
it slip.

### IMP-04 — Structured-404 coverage is intentionally narrow but undocumented

**Files:**
- `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:36-40` (503 plain string)
- `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:48` (503 plain string)
- `/home/bernt-popp/development/genereviews-link/docs/superpowers/specs/2026-05-11-mcp-llm-ergonomics-design.md:625-633` (spec table)

**Issue.** The spec's error-handling table (Component 4 + the table at
the end of "Error handling") classifies 503-misconfig as a 4xx-recoverable
class and says it should carry `MCPErrorPayload`. The implementation
leaves the 503s as plain `HTTPException(detail="...")` — a defensible
call (it's a deployment problem, not LLM-recoverable), but it diverges
from what the spec literally says.

The 422s from the path/query Literal validators ARE in the right shape
already (FastAPI emits a structured detail with `loc`, `msg`, `ctx`),
but the spec implies an LLM-recoverable bias for those too. Live test:
the misspelled section returns a 422 with `ctx.expected` listing the
nine valid values — that IS LLM-recoverable, just not in the
`MCPErrorPayload` shape.

**Fix.** Either:
1. Wrap the 503s in `StructuredHTTPException(code="repository_unavailable",
   message=..., recovery_hint="DATABASE_URL must be set; this is a
   deployment-side fix")` — preserving the "operator-recoverable not
   LLM-recoverable" framing through the `recovery_hint` text. ~6 LOC,
   2 routes.
2. Update the spec text to explicitly call out that 503-misconfig and
   422-validation are intentional plain shapes.

Either resolves the divergence. Option 1 is slightly nicer because the
MCP client gets a uniform `code` field across all error classes.

---

## Minor findings (6)

### MIN-01 — `find_in_section` prompt's `section` argument has no description in `prompts/list`

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/mcp/prompts.py:10-25`

**Live observation.** The MCP `prompts/list` response gives `gene_symbol`
a description (`"Provide as a JSON string matching the following schema:
{\"type\":\"string\"}"`) but `section` has no description at all. This
is FastMCP's default rendering for `Literal[...]` types — apparently it
falls back to omitting `description` rather than emitting the enum
values. An LLM picking this prompt with no client-side schema validation
has no way to know which section names are valid without calling
`tools/list` and pattern-matching.

**Fix.** Use `pydantic.Field` (or FastMCP's `Annotated`-style description
override) to attach an explicit description, e.g.
`def find_in_section(gene_symbol: Annotated[str, "HGNC gene symbol like 'BRCA1'"], section: Annotated[SectionName, f"One of: {', '.join(SECTION_NAMES)}"]) -> str:`.

### MIN-02 — `cast(SectionName, ...)` masks a real (rare) failure mode

**Files:**
- `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:179,255`
- `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/debug.py:61`

**Issue.** `PassageRow.chapter_section` is `str` (sourced from a `text`
column with no `CHECK` constraint — verified at
`migrations/data/0002_passages.sql:4`). The three routes use
`cast(SectionName, row.chapter_section)` to satisfy mypy, then construct
`RankedPassage` / `PassageDetail`. Pydantic DOES validate at runtime
(verified: `PassageDetail(chapter_section='managment', ...)` raises
`ValidationError`), so a corrupted DB row would 500 the route with an
opaque traceback rather than returning the row. The PR body documents
this as a follow-up.

**Fix (one of):**
1. Add a Postgres `CHECK (chapter_section in (...))` migration. Hardest
   guarantee but requires a migration.
2. Validate at the repository hydration boundary — convert
   `PassageRow.chapter_section: str` → `PassageRow.chapter_section: SectionName`
   and let `_to_passage_row()` raise on unknown values, surfaced as a
   500 with a clearer message and a structured log line. ~10 LOC.
3. Tolerate unknown values by mapping them to `"other"` at hydration.
   Wrong-but-safe. Worst option for data integrity.

I'd take #2.

### MIN-03 — `exclude=` JSONResponse path drops `corpus_version` plumbing

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:198-205`

**Issue.** Local block:
```python
if exclude:
    excluded: set[str] = {str(field) for field in exclude}
    return JSONResponse({
        "results": [...],
        "_meta": ResponseMeta().model_dump(),  # always default — no corpus_version arg
    })
```
The non-exclude path passes `ResponseMeta(corpus_version=corpus)`. Today
both produce identical output (corpus is always `None`), but if/when
IMP-02 is fixed, the `exclude=` path will silently drop the version
string. Easy to miss in review.

**Fix.** Hoist a single `meta = ResponseMeta(corpus_version=corpus)` and
share between both branches.

### MIN-04 — `PassageSearchResponse.model_validate({...})` workaround is unnecessary

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/api/routes/passages.py:207-212`

**Issue.** The non-exclude return uses
`PassageSearchResponse.model_validate({"results": out, "_meta": ResponseMeta(...)})`.
With `populate_by_name=True` on the model, the cleaner form
`PassageSearchResponse(results=out, meta=ResponseMeta(corpus_version=corpus))`
works (using the Python attr name, not the alias). Verified: same
serialised output. The `model_validate` form is fine but feels like an
artifact of someone fighting the alias rules and giving up — worth
tightening.

**Fix.** Replace with the direct constructor call.

### MIN-05 — `PassageSearchResponse._meta` and `ChapterSectionResponse._meta` not in OpenAPI `required`

**Live observation.** The OpenAPI schema lists `_meta` as a property but
not in `required` for either envelope. Because `default_factory=ResponseMeta`
populates it server-side, in practice `_meta` is always present. But a
strict downstream consumer (or a code generator) would treat `_meta` as
optional and possibly skip it. Pydantic emits this correctly for fields
with defaults — there is no real bug here, but consumers of the OpenAPI
schema may not realise the field is in fact always present.

**Fix.** Either accept it (it IS technically optional in the schema
sense) or use `Field(alias="_meta", default_factory=ResponseMeta, ...)`
with an explicit `json_schema_extra={"required": True}` if you want to
force it. Low priority.

### MIN-06 — Phase 4 structured 404 at MCP layer arrives as Python `repr`, not JSON

**Live observation.** `tools/call` for `get_passage` with an unknown id
returns:
```json
{"content": [{"type":"text","text":"Error calling tool 'get_passage': HTTP error 404: Not Found - {'detail': {'code': 'passage_not_found', ...}}"}], "isError": true}
```
That `{'detail': {'code': '...', ...}}` is Python `dict.__repr__`
output (single quotes, not double) — not parseable JSON. So an LLM
trying to parse `tool_result.content[0].text` to extract `next_commands`
or `recovery_hint` would have to either eval the Python repr or do
fuzzy regex extraction.

This is **FastMCP's behaviour**, not anything this PR introduced — the
original `HTTPException` rendering path predates the spec. The structured
404 IS still useful (the LLM can read the message text and `recovery_hint`
substring) but the "machine-parseable structured payload" promise of
Component 4 is technically only delivered for direct HTTP consumers
(REST), not for the MCP transport.

**Fix.** This is upstream-FastMCP territory. Two paths:
1. Filed-issue / contribution to FastMCP to render `HTTPException.detail`
   as JSON when the detail is a dict.
2. Workaround locally: catch `StructuredHTTPException` in a route-level
   handler and return `JSONResponse(status_code=..., content={...})`
   directly so FastMCP's HTTPX-bridge path serialises it cleanly.

Out of scope for this PR; flag as follow-up.

---

## Nits (5)

### NIT-01 — Spec/plan sit in `docs/superpowers/{specs,plans}/` but live reviews expected at `docs/superpowers/reviews/`

**Live observation.** That directory did not exist in the repo before
this review (created by this run). Adding a stub `reviews/README.md`
or a `.gitkeep` would help — current convention is implicit.

### NIT-02 — En-dash typography enforced via `# noqa: RUF001` twice

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/models/genereview_models.py:145,158`

The PR notes call this out. Acceptable. Consider a project-level Ruff
config exception for these specific copyright-string sites if the
pattern recurs in future content additions.

### NIT-03 — `recall_query` SQL has a redundant `$1::text as _ignored` slot

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/retrieval/repository.py:152,205`

The `q` CTE includes `$1::text as _ignored` to keep the parameter
positional ordering stable when `brief=True`. It works but reads as
unnecessary scaffolding; a comment explaining why it's there would help
future maintainers.

### NIT-04 — Unused symbol re-export in `tests/test_response_envelope_models.py`

**File:** `/home/bernt-popp/development/genereviews-link/tests/test_response_envelope_models.py:7`

`ChapterSectionResponse` is imported with `# noqa: F401  # re-exported
smoke import` but never used. Either add a smoke test that asserts it
constructs cleanly, or drop the import.

### NIT-05 — `LicenseNotice.copyright` and `ATTRIBUTION_TEXT` share the en-dash but not the same pattern

**File:** `/home/bernt-popp/development/genereviews-link/genereview_link/models/genereview_models.py:144-160`

The spec called for "single source of truth — updating one copyright
string updates both the envelope and the `/license` tool." Currently
`ATTRIBUTION_TEXT` is a longer prose string and `LicenseNotice.copyright`
is the short copyright line. They are not derived from each other; if
the year prefix needs to change, both literal strings must be edited.
A small constant
`COPYRIGHT_LINE = "© 1993–present University of Washington"` shared
between them would honour the spec's wording more literally.

---

## Documentation drift

- `README.md` is not updated to mention `find_in_section`, `get_passage`,
  the `mode=brief` default, or the `_meta.attribution` envelope. The
  `mcp-tool-change` skill at `.claude/skills/mcp-tool-change/SKILL.md`
  explicitly says "Update README and AGENTS.md if tool names or scopes
  change" — this PR adds one tool (`get_passage`) and one prompt
  (`find_in_section`) but neither doc is updated. **Recommend a
  README "MCP tool surface" section listing the nine current tools and
  one prompt.**
- `AGENTS.md` is fine — its description of the project remains accurate.

---

## Test coverage gaps (informational)

The per-task tests are thorough at the unit level. Two gaps remain:

1. **No end-to-end test that asserts `_meta.attribution` is byte-identical
   between the typed envelope path and the `exclude=` JSONResponse path.**
   I verified this manually against the live container. A two-line test
   that fetches `/passages/search?q=BRCA1` and `/passages/search?q=BRCA1&exclude=score_breakdown`
   and asserts `body['_meta']['attribution']` matches across both would
   pin MIN-03's invariant.
2. **No test exercises `tools/call` dispatch through the FastMCP HTTP
   layer.** `test_mcp_tool_dispatch.py` tests the FastAPI route, the
   `mcp_built_against` invariant, and the `instructions` plumbing — but
   not the actual MCP request-response round-trip. Adding a single
   `httpx.AsyncClient` test that POSTs an MCP `tools/call` JSON-RPC
   payload to a test app's mounted `/mcp/` would catch IMP-01 (default
   server stack misses MCP) and MIN-06 (404 rendering through MCP) at
   CI time. This is non-trivial because of the lifespan chaining; could
   be deferred.

---

## Summary table

| Severity | Count |
|---|---|
| Critical | 0 |
| Important | 4 |
| Minor | 6 |
| Nit | 5 |
| **Total** | **15** |

Of the four Important findings:
- **IMP-01** (default Docker doesn't mount `/mcp/`) is a deployment-config
  gap that should be fixed before declaring the ergonomics work "shipped"
  to public clients. Single-line CMD change.
- **IMP-02** (`corpus_version` always null) is a 15-LOC fix that
  delivers a feature the spec said was in scope.
- **IMP-03** (gene_symbols duplication) is mechanical cleanup, ~10 LOC.
- **IMP-04** (structured-error coverage) is a small spec-vs-implementation
  divergence — fixable by either docs or a 6-LOC route change.

None block merge; all are addressable in a small follow-up commit.

---

_Reviewed: 2026-05-11_
_Reviewer: Claude Opus 4.7 (1M ctx) — gsd-code-reviewer (deep)_
_Live container probed: http://127.0.0.1:8765 (gr-pg override)_
_Tests run: make ci-local (217 passed, 37 warnings)_
