# Group B API Ergonomics And Scraper Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Tier 1 Group B issues (#34, #39, #29, #36, #38, #37) in a single PR
analogous to Group A. Fix two scraper bugs, one operational bug, one empty-section
ergonomics gap, one tool-description cross-reference gap, and one orchestration
size foot-gun. No schema changes.

**Architecture:** Surgical edits to `eutils_client.py`, `repository.py`,
`chapters.py`, `genereview.py`, two model additions to `ChapterSectionResponse`
and `ResponseMeta`, Dockerfile + compose edits, and a final wording pass on
route descriptions and `genereview://usage`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, asyncpg-backed repository,
existing `EutilsClient`, pytest, Ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-23-group-b-ergonomics-bundle.md`

**Branch:** `feat/group-b-ergonomics-bundle`

---

## File Map

**Modify:**
- `genereview_link/api/eutils_client.py` — drop `.join` on references (#34); add
  `unicodedata.normalize` and explicit Unicode-space replace in `_clean_content` (#39).
- `genereview_link/retrieval/repository.py` — reword `_note_for_empty_section` to
  name `get_abstract` (#36).
- `genereview_link/api/routes/chapters.py` — attach `next_commands` to the empty
  `SYSTEMATICALLY_UNSCRAPED_SECTIONS` response (#36); add `search_passages`
  cross-reference to `get_chapter_metadata` and `get_chapter_section` route
  summary/description strings (#38).
- `genereview_link/api/routes/genereview.py` — flip `include_fulltext` default to
  `False`, add `max_chars` Query param, surface `_meta.truncated` and
  `_meta.next_commands` on truncation (#37).
- `genereview_link/api/routes/abstract.py` — tighten description to name the
  value-add over raw E-utils (#38).
- `genereview_link/api/routes/links.py` — same as abstract.py (#38).
- `genereview_link/models/genereview_models.py` — add optional `next_commands`
  field to `ChapterSectionResponse` (#36); add optional `truncated` and
  `next_commands` fields to `ResponseMeta` (#37).
- `genereview_link/api/resources/usage.py` — add `search_passages(q, nbk_id=...)`
  step in the canonical pipeline (#38).
- `docker/Dockerfile` — remove image-level `HEALTHCHECK` directive (#29).
- `docker/docker-compose.yml` — add documented `genereview-link-embed` service
  under `profiles: ["embed"]` with `healthcheck: disable: true` (#29).
- `README.md` (or `docker/README.md`) — document the `--profile embed` invocation
  (#29).

**Create:**
- `tests/unit/test_eutils_client_references.py` — references-list contract test (#34).
- `tests/unit/test_clean_content_unicode.py` — Unicode whitespace regression (#39).
- `tests/unit/test_dockerfile_healthcheck.py` — assert no `HEALTHCHECK` directive
  remains in `docker/Dockerfile` (#29).
- `tests/test_chapter_empty_section_next_commands.py` — empty summary section
  attaches `get_abstract` next_command (#36).
- `tests/test_genereview_fulltext_guardrail.py` — `include_fulltext` default
  flip, `max_chars` cap, truncation flagging (#37).

**Extend:**
- `tests/test_tool_schema_descriptions.py` — assert chapter / abstract / links
  route descriptions name `search_passages` or value-add wording (#38).
- `tests/test_mcp_license_resource.py` (or a sibling usage-resource test) —
  assert `genereview://usage` Pipeline section mentions
  `search_passages(q, nbk_id=...)` (#38).

---

### Task 1: Fix references-list type bug (#34)

**Files:**
- Modify: `genereview_link/api/eutils_client.py:791`
- Create: `tests/unit/test_eutils_client_references.py`

- [ ] **Step 1: Write the contract test first**

Add a test that loads a fixture HTML containing a recognizable references section,
calls `EutilsClient._extract_metadata(...)`, and asserts the returned
`metadata["references"]` is a non-empty `list[str]`:

```python
from pathlib import Path

from bs4 import BeautifulSoup

from genereview_link.api.eutils_client import EutilsClient


def test_extract_metadata_references_is_list_of_strings() -> None:
    fixture = Path("tests/fixtures/bookshelf_nbk1247_references.html").read_text()
    soup = BeautifulSoup(fixture, "html.parser")
    content_div = soup.find("div", id="maincontent") or soup
    client = EutilsClient()
    metadata = client._extract_metadata(content_div)
    refs = metadata.get("references")
    assert isinstance(refs, list)
    assert all(isinstance(r, str) for r in refs)
    assert len(refs) > 0
```

If a usable fixture does not yet exist under `tests/fixtures/`, capture a small
synthetic HTML snippet inline rather than fetching live.

- [ ] **Step 2: Apply the one-line fix**

In `genereview_link/api/eutils_client.py:791`:

```python
references = self._extract_references(content_div)
if references:
    metadata["references"] = references  # was: "\n".join(references)
```

- [ ] **Step 3: Add a route-level regression**

Extend the existing fulltext route test to assert
`response_json["metadata"]["references"]` is a JSON array, not a string.

- [ ] **Step 4: Run focused tests**

```bash
make test-fast
```

Expected: new tests pass; nothing else regresses.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/unit/test_eutils_client_references.py
git commit -m "fix(scraper): preserve references as list[str] (#34)"
```

---

### Task 2: Unicode-aware whitespace normalization (#39)

**Files:**
- Modify: `genereview_link/api/eutils_client.py:1285-1299`
- Create: `tests/unit/test_clean_content_unicode.py`

- [ ] **Step 1: Write the regression test first**

```python
from genereview_link.api.eutils_client import EutilsClient


def test_clean_content_normalizes_unicode_spaces() -> None:
    client = EutilsClient()
    # NBSP, thin space, narrow no-break space embedded around inline tags
    raw = "<i>BRCA1</i> - Associated HBOC"
    out = client._clean_content(raw)
    assert " " not in out
    assert " " not in out
    assert " " not in out
    assert "  " not in out  # no double spaces


def test_clean_content_is_idempotent() -> None:
    client = EutilsClient()
    raw = "<i>BRCA1</i> - Associated  text  "
    once = client._clean_content(raw)
    twice = client._clean_content(once)
    assert once == twice
```

- [ ] **Step 2: Extend `_clean_content`**

Add `import unicodedata` at the top of the file if not present, and update
`_clean_content`:

```python
def _clean_content(self, content: str) -> str:
    content = re.sub(r"<[^>]+>", "", content)
    content = re.sub(r"&[a-zA-Z0-9#]+;", "", content)
    content = unicodedata.normalize("NFKC", content)
    content = content.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    content = re.sub(r"\s+", " ", content)
    content = re.sub(r"(\n\s*){3,}", "\n\n", content)
    content = re.sub(r"\s*(Show details|Hide details)\s*", "", content, flags=re.I)
    content = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", content)
    return content.strip()
```

- [ ] **Step 3: Run focused tests**

```bash
make test-fast
```

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/unit/test_clean_content_unicode.py
git commit -m "fix(scraper): normalize NBSP and Unicode whitespace in _clean_content (#39)"
```

---

### Task 3: Remove image-level healthcheck + add embed compose profile (#29)

**Files:**
- Modify: `docker/Dockerfile:66-67`
- Modify: `docker/docker-compose.yml`
- Modify: `README.md` or `docker/README.md` (whichever already documents compose)
- Create: `tests/unit/test_dockerfile_healthcheck.py`

- [ ] **Step 1: Write the Dockerfile contract test**

```python
from pathlib import Path


def test_dockerfile_has_no_image_level_healthcheck() -> None:
    dockerfile = Path("docker/Dockerfile").read_text()
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.upper().startswith("HEALTHCHECK"), (
            "Image-level HEALTHCHECK applies to every command run from the "
            "image, including 'genereview-link embed', and causes spurious "
            "unhealthy status. Define healthcheck per service in compose."
        )
```

- [ ] **Step 2: Remove the Dockerfile HEALTHCHECK**

Delete lines 66-67 of `docker/Dockerfile`:

```
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
```

- [ ] **Step 3: Add the embed profile to compose**

Append to `docker/docker-compose.yml` after the `genereview-link` service:

```yaml
  genereview-link-embed:
    image: ${IMAGE:-docker-genereview-link}
    profiles: ["embed"]
    command: ["genereview-link", "embed"]
    env_file: ../.env.docker
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-genereview}:${POSTGRES_PASSWORD}@postgres:5432/genereview
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      disable: true
```

If `docker/docker-compose.yml` uses `build:` instead of `image:` for the API
service, mirror that shape on the embed service. The goal is the embed service
shares the same image artifact, not that it rebuilds.

- [ ] **Step 4: Document the invocation**

In `README.md` (or `docker/README.md` if that is the canonical Docker doc),
add a one-line example near other compose examples:

```markdown
### Embedding backfill

Run as a one-off, healthcheck-disabled compose service:

    docker compose --profile embed up genereview-link-embed
```

- [ ] **Step 5: Validate compose syntax**

```bash
docker compose -f docker/docker-compose.yml config >/dev/null
docker compose -f docker/docker-compose.yml --profile embed config | grep genereview-link-embed
```

Expected: no errors, the embed service appears only when `--profile embed` is
passed.

- [ ] **Step 6: Run focused tests**

```bash
make test-fast
```

- [ ] **Step 7: Commit**

```bash
git add docker/Dockerfile docker/docker-compose.yml README.md tests/unit/test_dockerfile_healthcheck.py
git commit -m "fix(docker): drop image-level healthcheck; add embed profile (#29)"
```

---

### Task 4: Empty summary section routes to `get_abstract` (#36)

**Files:**
- Modify: `genereview_link/models/genereview_models.py` (add `next_commands` to
  `ChapterSectionResponse`).
- Modify: `genereview_link/retrieval/repository.py:882-892` (reword note).
- Modify: `genereview_link/api/routes/chapters.py:142-152` (attach next_commands).
- Create: `tests/test_chapter_empty_section_next_commands.py`

- [ ] **Step 1: Write the route test first**

```python
from fastapi.testclient import TestClient


def test_empty_summary_section_routes_to_get_abstract(client: TestClient) -> None:
    response = client.get("/chapters/NBK1247/sections/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["passage_count"] == 0
    assert body["passages"] == []
    note = body["note"]
    assert "get_abstract" in note
    next_commands = body["next_commands"]
    assert next_commands[0]["tool"] == "get_abstract"
    assert "pubmed_id" in next_commands[0]["arguments"]


def test_empty_summary_section_omits_next_commands_without_pubmed_id(
    client: TestClient,
) -> None:
    # Use a chapter known to have no pubmed_id mapping, or mock the repo.
    ...
```

- [ ] **Step 2: Add the optional `next_commands` field to `ChapterSectionResponse`**

In `genereview_link/models/genereview_models.py`, add to `ChapterSectionResponse`:

```python
next_commands: list[dict[str, Any]] | None = None
```

Add the `Any` import if not already present.

- [ ] **Step 3: Reword `_note_for_empty_section`**

In `genereview_link/retrieval/repository.py:882-892`:

```python
def _note_for_empty_section(section: str, nbk_id: str) -> str | None:
    if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
        return (
            f"section {section!r} is not scraped from NCBI Bookshelf NXML; "
            f"call get_abstract(pubmed_id=<chapter.pubmed_id>) for the chapter "
            f"abstract (or open https://www.ncbi.nlm.nih.gov/books/{nbk_id}/)."
        )
    return None
```

- [ ] **Step 4: Attach `next_commands` in the empty-section branch**

In `genereview_link/api/routes/chapters.py:142-152`, replace the
`SYSTEMATICALLY_UNSCRAPED_SECTIONS` return with:

```python
if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
    return ChapterSectionResponse(  # type: ignore[call-arg]
        nbk_id=nbk_id,
        chapter_title=chapter.title,
        chapter_section=section,
        chapter_last_updated=chapter.last_updated_date,
        passages=[],
        passage_count=0,
        note=_note_for_empty_section(section, nbk_id),
        next_commands=(
            [{"tool": "get_abstract", "arguments": {"pubmed_id": chapter.pubmed_id}}]
            if chapter.pubmed_id
            else None
        ),
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
```

If `chapter.pubmed_id` is not directly accessible from the repository's chapter
shape, fetch it via the same repository call used elsewhere in the file. Avoid
emitting a command with `pubmed_id: None`.

- [ ] **Step 5: Run focused tests**

```bash
make test-fast
```

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/retrieval/repository.py genereview_link/api/routes/chapters.py tests/test_chapter_empty_section_next_commands.py
git commit -m "feat(api): route empty summary section to get_abstract via next_commands (#36)"
```

---

### Task 5: `get_genereview_summary` size guardrail (#37)

**Files:**
- Modify: `genereview_link/models/genereview_models.py` (add `truncated` and
  `next_commands` to `ResponseMeta`).
- Modify: `genereview_link/api/routes/genereview.py:47-101` (flip default, add
  `max_chars`, truncate, flag).
- Modify: `genereview_link/services/genereview_service.py` if truncation is
  applied in the service layer rather than the route.
- Create: `tests/test_genereview_fulltext_guardrail.py`

- [ ] **Step 1: Write the route tests first**

```python
def test_default_excludes_fulltext(client) -> None:
    r = client.get("/genereview/BRCA1")
    assert r.status_code == 200
    body = r.json()
    assert body.get("fulltext") in (None, {})
    meta = body.get("_meta") or body.get("meta", {})
    assert meta.get("truncated") in (None, False)


def test_explicit_include_fulltext_with_default_max_chars(client) -> None:
    r = client.get("/genereview/BRCA1?include_fulltext=true")
    assert r.status_code == 200
    body = r.json()
    assert body["fulltext"]
    # Default cap is 16000; large chapters truncate
    meta = body.get("_meta") or body.get("meta", {})
    if meta.get("truncated"):
        assert meta["next_commands"][0]["tool"] == "get_chapter_section"


def test_max_chars_zero_disables_cap(client) -> None:
    r = client.get("/genereview/BRCA1?include_fulltext=true&max_chars=0")
    assert r.status_code == 200
    meta = r.json().get("_meta") or {}
    assert not meta.get("truncated")


def test_small_max_chars_forces_truncation(client) -> None:
    r = client.get("/genereview/BRCA1?include_fulltext=true&max_chars=100")
    body = r.json()
    meta = body.get("_meta") or {}
    assert meta.get("truncated") is True
    assert meta["next_commands"][0]["tool"] == "get_chapter_section"
    assert meta["next_commands"][0]["arguments"]["nbk_id"]
```

- [ ] **Step 2: Extend `ResponseMeta`**

In `genereview_link/models/genereview_models.py`, add to `ResponseMeta`:

```python
truncated: bool = False
next_commands: list[dict[str, Any]] | None = None
```

The `Any` import may already be present from Task 4.

- [ ] **Step 3: Flip default + add `max_chars` to the route signature**

In `genereview_link/api/routes/genereview.py:47-55`:

```python
include_fulltext: bool = Query(
    False,
    description=(
        "Default False: response is lean. Opt in for chapter prose. "
        "Truncation is governed by max_chars."
    ),
),
max_chars: int = Query(
    16000,
    ge=0,
    le=200000,
    description=(
        "Cap fulltext payload size in characters when include_fulltext=true. "
        "Pass 0 to disable the cap. Truncated responses set _meta.truncated=true "
        "and surface next_commands -> get_chapter_section."
    ),
),
```

- [ ] **Step 4: Apply truncation + stamp `_meta`**

After the service call returns the `GeneReview` result, when
`include_fulltext` is true:

```python
if include_fulltext and max_chars > 0 and result.fulltext and result.fulltext.text:
    full = result.fulltext.text
    if len(full) > max_chars:
        result.fulltext.text = full[:max_chars]
        result.meta.truncated = True
        nbk = result.nbk_id if hasattr(result, "nbk_id") else None
        section_hint = "management"  # safe default; or pick from result if known
        result.meta.next_commands = [
            {
                "tool": "get_chapter_section",
                "arguments": (
                    {"nbk_id": nbk, "section": section_hint}
                    if nbk
                    else {"section": section_hint}
                ),
            }
        ]
```

Pick the section hint from whichever section in `result` is most likely the
truncated portion; if no clean signal exists, omit the `section` argument and
emit only `nbk_id`. The plan-phase executor should confirm the actual response
model shape before locking the snippet above.

- [ ] **Step 5: Update the route description**

Replace `description=` on the `@router.get` block:

```python
description=(
    "Convenience orchestration tool. Default response is lean: include_fulltext "
    "defaults to False; opt in for full chapter prose. max_chars (default 16000) "
    "truncates fulltext to keep responses context-budget friendly; truncated "
    "responses surface next_commands -> get_chapter_section. Resolves "
    "gene -> PubMed -> NBK using local corpus when available; falls back to "
    "live NCBI services. fresh=true bypasses indexed context."
),
```

- [ ] **Step 6: Run focused tests**

```bash
make test-fast
```

- [ ] **Step 7: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/genereview.py genereview_link/services/genereview_service.py tests/test_genereview_fulltext_guardrail.py
git commit -m "feat(api): add max_chars guardrail to get_genereview_summary; default include_fulltext=False (#37)"
```

Note: this commit is a deliberate behavior change. The PR body must call it
out as the single Group B breaking change for default callers.

---

### Task 6: Cross-reference `search_passages` in tool descriptions (#38)

**Files:**
- Modify: `genereview_link/api/routes/chapters.py:60-67, 209-215` (descriptions).
- Modify: `genereview_link/api/routes/abstract.py:32-38` (description).
- Modify: `genereview_link/api/routes/links.py:28-34` (description).
- Modify: `genereview_link/api/resources/usage.py:12-18` (Pipeline section).
- Extend: `tests/test_tool_schema_descriptions.py` (or create if absent).
- Extend: `tests/test_mcp_license_resource.py` (or sibling usage-resource test).

- [ ] **Step 1: Write the description contract tests first**

```python
from genereview_link.api.routes import chapters, abstract, links
from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN


def _route_description(router, operation_id: str) -> str:
    for route in router.routes:
        if getattr(route, "operation_id", None) == operation_id:
            return route.description or ""
    raise AssertionError(f"route {operation_id} not found")


def test_get_chapter_metadata_mentions_search_passages() -> None:
    desc = _route_description(chapters.router, "get_chapter_metadata")
    assert "search_passages" in desc


def test_get_chapter_section_mentions_search_passages() -> None:
    desc = _route_description(chapters.router, "get_chapter_section")
    assert "search_passages" in desc


def test_get_abstract_describes_value_add() -> None:
    desc = _route_description(abstract.router, "get_abstract")
    assert "normalized" in desc.lower() or "structured" in desc.lower()


def test_get_links_describes_value_add() -> None:
    desc = _route_description(links.router, "get_links")
    assert "normalized" in desc.lower() or "structured" in desc.lower()


def test_usage_resource_pipeline_includes_in_chapter_search() -> None:
    assert "search_passages(q, nbk_id=" in USAGE_RESOURCE_MARKDOWN
```

- [ ] **Step 2: Update chapter route descriptions**

`chapters.py:214`:

```python
summary=(
    "The chapter outline tool: title, dates, gene symbols, section counts, "
    "and tables. Use search_passages(q, nbk_id=...) for keyword search "
    "within this chapter."
),
```

`chapters.py:61-66`:

```python
description=(
    "Fetch all passages for a section. For keyword search within this "
    "section, use search_passages(q, nbk_id=..., sections=[...]); for "
    "joined section text use include=concatenated_text. Pass dedupe=false "
    "only for literal chunk text."
),
```

- [ ] **Step 3: Tighten abstract / links descriptions**

`abstract.py:33`:

```python
description=(
    "Live NCBI E-utils abstract wrapper. Adds normalized response shape, "
    "structured error envelopes, and active corpus-version stamping over a "
    "raw efetch call. Default responses may carry active _meta.corpus_version "
    "context; fresh=true labels the response version as live:<timestamp>."
),
```

`links.py:29`:

```python
description=(
    "Live NCBI E-utils link wrapper. Adds normalized categorization, "
    "structured error envelopes, and active corpus-version stamping over a "
    "raw elink call. Default responses may carry active _meta.corpus_version "
    "context; fresh=true labels the response version as live:<timestamp>."
),
```

- [ ] **Step 4: Update `genereview://usage` Pipeline section**

In `genereview_link/api/resources/usage.py:12-18`:

```markdown
## Pipeline

`search_passages` (brief mode) -> `get_chapter_metadata(nbk_id)` to read
title, last_updated_date, gene_symbols, per-section passage_count and
total_char_count, and the full list of tables -> `search_passages(q, nbk_id=...)`
for in-chapter content search -> `get_passage(passage_id)` OR
`get_chapter_section(nbk_id, section)` OR `get_table(nbk_id, table_id)` OR
`get_passages_batch(ids=[...])` for up to 20 passage_ids at once.
```

- [ ] **Step 5: Run focused tests**

```bash
make test-fast
```

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/routes/chapters.py genereview_link/api/routes/abstract.py genereview_link/api/routes/links.py genereview_link/api/resources/usage.py tests/test_tool_schema_descriptions.py tests/test_mcp_license_resource.py
git commit -m "docs(api): cross-reference search_passages from chapter and live tools (#38)"
```

---

### Task 7: Full CI + PR

- [ ] **Step 1: Run `make ci-local`**

```bash
make ci-local
```

Expected: format, lint, typecheck, and tests all green. Investigate and fix any
real failures. Do not bypass with `--no-verify`.

- [ ] **Step 2: Confirm closing-list comment readiness**

Each of the six commits should reference its issue number in the message
footer (already done in steps above). The PR body should list:

```
Closes #34
Closes #39
Closes #29
Closes #36
Closes #38
Closes #37
```

- [ ] **Step 3: Push branch and open PR**

```bash
git push -u origin feat/group-b-ergonomics-bundle
gh pr create \
  --title "feat: Group B API ergonomics + scraper polish bundle" \
  --body "$(cat <<'EOF'
## Summary

Tier 1 LLM-ergonomics + bug bundle, mirror of PR #48 for Group A. Six issues, one PR.

- #34 fix(scraper): `metadata.references` now preserved as `list[str]` (was joined to a string and silently dropped).
- #39 fix(scraper): `_clean_content()` runs `unicodedata.normalize("NFKC")` and explicitly replaces NBSP / thin space / narrow no-break space. No more double spaces around inline gene names.
- #29 fix(docker): removed image-level `HEALTHCHECK` from `docker/Dockerfile`. Added documented `genereview-link-embed` service under `profiles: ["embed"]` with healthcheck disabled. Embedding backfill containers no longer show `unhealthy`.
- #36 feat(api): empty `summary` section response now names `get_abstract` in `note` and attaches `next_commands -> get_abstract(pubmed_id=...)`.
- #38 docs(api): chapter tool descriptions cross-reference `search_passages(q, nbk_id=...)`; abstract / links descriptions name their value-add over raw E-utils; `genereview://usage` Pipeline includes the in-chapter search step.
- #37 feat(api): `get_genereview_summary` `include_fulltext` defaults to **False**; added `max_chars` (default 16000) with `_meta.truncated` flag and `next_commands -> get_chapter_section`. **One breaking change for default callers** — see below.

## Breaking change

`GET /genereview/{gene_symbol}` no longer returns fulltext by default. Callers that depended on the implicit `include_fulltext=true` must add `?include_fulltext=true`. Truncation at `max_chars` is also new; pass `max_chars=0` to disable.

## Test plan

- [ ] `make ci-local` passes
- [ ] `GET /chapters/NBK1247/sections/summary` shows `get_abstract` in `note` and `next_commands`
- [ ] `GET /genereview/BRCA1` default is lean (no fulltext)
- [ ] `GET /genereview/BRCA1?include_fulltext=true&max_chars=100` truncates and stamps `_meta.truncated=true`
- [ ] `docker run --rm docker-genereview-link genereview-link embed` reports `running`, not `unhealthy`
- [ ] `docker compose --profile embed up genereview-link-embed` starts cleanly

Closes #34
Closes #39
Closes #29
Closes #36
Closes #38
Closes #37

Spec: docs/superpowers/specs/2026-05-23-group-b-ergonomics-bundle.md
Plan: docs/superpowers/plans/2026-05-23-group-b-ergonomics-bundle.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Watch CI**

```bash
gh pr checks --watch
```

Expected: all required checks green within ~10 minutes.

---

## Execution Order Rationale

1. **#34 first** (smallest diff, baseline confidence).
2. **#39 second** (same file as #34, related semantic concept).
3. **#29 third** (isolated to docker/, can land independently if CI hiccups).
4. **#36 fourth** (introduces optional `next_commands` field on
   `ChapterSectionResponse` — first model touch).
5. **#37 fifth** (introduces optional `truncated` + `next_commands` on
   `ResponseMeta` — second model touch; behavior change so it benefits from
   the four preceding green commits).
6. **#38 last** (pure wording pass — easy to amend if earlier tasks shift
   descriptions; final coherence pass on the public API surface).

## Risk Notes

- **Pydantic field ordering on `ResponseMeta` and `ChapterSectionResponse`.** Both
  models are `BaseModel`; adding optional fields with defaults is non-breaking.
  Confirm no `model_config` enforces `extra="forbid"` on consuming tests.
- **Compose service image name.** If `docker/docker-compose.yml` uses a
  computed `image:` name from `${IMAGE_TAG}` or similar, mirror exactly on the
  embed service so they share the artifact.
- **Truncation section-hint.** Task 5 Step 4 picks `"management"` as a default
  section to suggest. If the response model already exposes a richer signal
  (e.g. which section the truncation actually cut into), prefer that. If not,
  omitting the `section` argument and surfacing only `nbk_id` is also acceptable.
- **`_extract_metadata` signature.** Task 1 Step 1 assumes `_extract_metadata`
  is callable directly with a `Tag`. Confirm during execution; if the method
  signature is different in current main, adapt the test setup accordingly.
- **`pubmed_id` on chapter shape.** Task 4 Step 4 assumes `chapter.pubmed_id`
  is accessible from the repository's chapter object in the empty-section
  branch. The Group A work added repository-first lookups that should make
  this true; confirm during execution and adjust the conditional accordingly.
