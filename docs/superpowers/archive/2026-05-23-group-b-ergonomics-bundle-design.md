# Group B API Ergonomics And Scraper Polish Design

**Date:** 2026-05-23
> Historical record

**Issues:** #34, #39, #29, #36, #38, #37
**Scope:** One remediation phase bundling Tier 1 LLM-ergonomics polish and
scraper bugs identified in the 2026-05-13 external LLM-MCP review.

This is the natural follow-on to PR #48 (Group A reliability). Group A locked
the orchestration contract; Group B closes the small, surgical quality issues
that surfaced in the same review and were deferred.

## Goal

Make the indexed-corpus + scraper surface area cleaner, smaller, and
self-routing for LLM clients, without touching the database schema:

- fix the two scraper bugs that produce wrong-shape data (#34 references,
  #39 NBSP / whitespace artefacts);
- fix the operational bug that mislabels embedding containers as unhealthy
  (#29);
- make the empty-summary affordance route LLM clients to the right tool
  (#36 -> `get_abstract`);
- cross-reference `search_passages` from chapter tool descriptions so callers
  do not need to discover the in-chapter search pattern by trial (#38);
- add a size guardrail to `get_genereview_summary` so `include_fulltext` is
  no longer a context-window foot-gun (#37).

## Problems Addressed

**#34 references list bug.** `_extract_references()` returns `list[str]` but
`eutils_client.py:791` joins the list into a single string before writing it to
`metadata["references"]`. `FullTextMetadata.references` is typed `list[str]`,
so the joined string fails Pydantic validation and the public response field
ends up empty for every chapter, even when references were extracted.

**#39 whitespace / NBSP residue.** `_clean_content()`
(`eutils_client.py:1285`) collapses runs of `\s+`, but does not normalize
Unicode whitespace (U+00A0 NBSP, U+2009 thin space, U+202F narrow no-break
space) and does not run `unicodedata.normalize("NFKC")`. The reviewer reports
`BRCA1  -  Associated` style double-spaces around inline italic gene names.
The artifacts bleed into LLM-pasted report text.

**#29 image-level healthcheck on embed containers.** `docker/Dockerfile:66-67`
defines `HEALTHCHECK CMD curl -fsS http://localhost:8000/health || exit 1`.
This applies to every container started from the image, including
`docker run ... genereview-link embed`. The embed command does not bind port
8000, so the healthcheck always fails and Docker reports the container as
`unhealthy` even while embeddings are actively being written.

**#36 empty summary section note.** `_note_for_empty_section()` in
`retrieval/repository.py:882` returns a note pointing to the NCBI Bookshelf
URL but never names the `get_abstract` MCP tool. LLM clients reading the
section response cannot route to the abstract tool without parsing the URL.
The empty-section branch in `chapters.py:142-152` also does not attach
`next_commands`, even though that field is now used everywhere else in the
codebase.

**#38 chapter tools do not cross-reference `search_passages`.**
`get_chapter_metadata` (`chapters.py:214`) and `get_chapter_section`
(`chapters.py:61-66`) descriptions do not mention `search_passages(q, nbk_id=...)`
as the in-chapter content-search escalation. `get_abstract` and `get_links`
descriptions do not explain their value-add over a raw E-utils call (caching,
normalization, structured errors, cross-reference enrichment).

**#37 `get_genereview_summary` size foot-gun.**
`api/routes/genereview.py:53` defaults `include_fulltext=True`. For a large
chapter such as NBK1247 the response easily exceeds 60-80 k tokens. There is
no `max_chars` parameter, no truncation flag, and the `_summary` operation
name suggests a lean object. A first-time caller hits a context-window wall
on the default path.

## Non-Goals

- No schema migration (the four staleness / primary-gene / batched-search /
  rows-on-passage features are Tier 2 or Tier 4, tracked separately).
- No response-breaking rename of existing fields.
- No new ingestion logic.
- No inline-citation parser in this bundle (#34 minimal fix only; the
  optional `inline_citations: list[str]` field stays a follow-up).
- No new compose service for backfill beyond the documented `profiles: ["embed"]`
  block; no Kubernetes manifests.
- No broader scraper rewrite — `_clean_content()` gets a targeted Unicode
  normalization pass, not a redesign.
- No changes to ranking, retrieval, or embedding behavior.

## Design

### #34 References list type fix

`genereview_link/api/eutils_client.py:791` becomes:

```python
references = self._extract_references(content_div)
if references:
    metadata["references"] = references  # was: "\n".join(references)
```

The route layer (`api/routes/fulltext.py:160`) already reads
`metadata_dict.get("references", [])` into `FullTextMetadata.references:
list[str]`. Pydantic v2 will accept the list-shaped value.

The `_extract_references()` heuristic itself is not redesigned in this bundle;
the brittleness reported by the reviewer is a separate, larger issue.

### #39 Unicode-aware whitespace normalization

`_clean_content()` (`eutils_client.py:1285`) gains an explicit Unicode pass
before the existing `\s+` collapse:

```python
import unicodedata

def _clean_content(self, content: str) -> str:
    content = re.sub(r"<[^>]+>", "", content)
    content = re.sub(r"&[a-zA-Z0-9#]+;", "", content)
    # NEW: NFKC normalization + explicit U+00A0, U+2009, U+202F replacement
    content = unicodedata.normalize("NFKC", content)
    content = content.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    content = re.sub(r"\s+", " ", content)
    content = re.sub(r"(\n\s*){3,}", "\n\n", content)
    content = re.sub(r"\s*(Show details|Hide details)\s*", "", content, flags=re.I)
    content = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", content)
    return content.strip()
```

`unicodedata` is in the standard library. The explicit replace pass is kept
even after `NFKC` because some narrow / no-break space characters can survive
NFKC under specific input shapes; the redundancy is cheap and defensive.

### #29 Remove image-level healthcheck + add embed compose profile

`docker/Dockerfile:66-67` deletes the `HEALTHCHECK` line entirely. The
compose API service in `docker/docker-compose.yml:47-52` and
`docker/docker-compose.prod.yml:43-48` retains its healthcheck — image-level
removal does not affect compose-level checks.

`docker/docker-compose.yml` gains a documented embed/backfill service under
a `profiles` block so it does not start by default:

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

The README / Docker docs add a one-line invocation example:
`docker compose --profile embed up genereview-link-embed`.

### #36 Empty summary note routes to `get_abstract`

`_note_for_empty_section()` in `retrieval/repository.py:882-892` is reworded:

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

The empty-section branch in `chapters.py:142-152` attaches `next_commands` to
the response. Two ways to do this without breaking the public response shape:

1. Add an optional `next_commands: list[dict[str, Any]] | None = None` field
   to `ChapterSectionResponse` (mirrors the field already on
   `StructuredHTTPException` and `TableResponse`-adjacent paths).
2. Look up the chapter's `pubmed_id` via the existing repository call already
   in scope at `chapters.py:131`, and populate:

```python
return ChapterSectionResponse(
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

If the chapter has no indexed `pubmed_id`, the `next_commands` field is
omitted rather than emitting a command with a `None` argument (mirrors the
Group A convention in `orchestration_errors.py`).

### #38 Cross-reference `search_passages` from chapter and live tools

Four wording updates:

1. `chapters.py:214` summary: add escalation hint.
   `"The chapter outline tool: title, dates, gene symbols, section counts, "`
   `"and tables. Use search_passages(q, nbk_id=...) for keyword search "`
   `"within this chapter."`

2. `chapters.py:61-66` description: same escalation, scoped to section reads.
   `"Fetch all passages for a section. For keyword search within this section, "`
   `"use search_passages(q, nbk_id=..., sections=[...]); for joined section "`
   `"text use include=concatenated_text."`

3. `abstract.py:33` description: name the value-add.
   `"Live NCBI E-utils abstract wrapper. Adds normalized response shape, "`
   `"structured error envelopes, and active corpus-version stamping over a "`
   `"raw efetch call. Default responses may carry active _meta.corpus_version "`
   `"context; fresh=true labels the response version as live:<timestamp>."`

4. `links.py:29` description: same shape as `abstract.py`.

The `genereview://usage` resource (`api/resources/usage.py:12-18`) gains a
single extra arrow in the canonical pipeline to call out the in-chapter
search escalation:

```
search_passages (brief mode) -> get_chapter_metadata(nbk_id) to read ... ->
search_passages(q, nbk_id=...) for in-chapter content search ->
get_passage / get_chapter_section / get_table / get_passages_batch
```

### #37 `get_genereview_summary` fulltext guardrail

Two changes to `api/routes/genereview.py`:

1. Flip the default: `include_fulltext: bool = Query(False, ...)`. Existing
   callers that want full text must opt in. This is the chosen approach
   (option a from the issue).

2. Add a `max_chars` query parameter that truncates fulltext payloads when
   `include_fulltext=true`:

```python
max_chars: int = Query(
    16000,
    ge=0,
    le=200000,
    description=(
        "Cap fulltext payload size in characters when include_fulltext=true. "
        "Pass 0 to disable the cap (not recommended for context-budgeted "
        "callers). Truncated responses set _meta.truncated=true and surface "
        "next_commands pointing at get_chapter_section for the full content."
    ),
),
```

When truncation fires, the response stamps:

- `_meta.truncated = true`
- a `next_commands` array on `ResponseMeta` (or on the response model) with:

```json
[
  {"tool": "get_chapter_section", "arguments": {"nbk_id": "<NBK>", "section": "<name>"}}
]
```

The route description gains a leading sentence:

`"Convenience orchestration tool. Default response is lean: include_fulltext "`
`"defaults to False; opt in for full chapter prose. max_chars (default 16000) "`
`"truncates fulltext to keep responses context-budget friendly; truncated "`
`"responses surface next_commands -> get_chapter_section."`

The orchestration helper that builds the response already stamps `_meta` via
`ResponseMeta`; this bundle adds `truncated: bool = False` and
`next_commands: list[dict[str, Any]] | None = None` fields to `ResponseMeta`
(or a `GeneReview` sibling, picked during plan-phase). The field is optional
and defaults to off, so non-truncated responses are byte-identical to the
current Group A shape.

## API Compatibility

- `#34, #39, #29`: bug fixes only. No public API shape change.
- `#36`: extends `ChapterSectionResponse` with an optional, default-`None`
  `next_commands` field. Reading clients ignore unknown / null fields; no
  breakage.
- `#38`: documentation-only changes to route descriptions and the
  `genereview://usage` resource. No code or response shape change.
- `#37`: **two breaking changes for the default code path**:
  1. `GET /genereview/{gene_symbol}` no longer returns `fulltext` by default.
     Callers that depended on the implicit `include_fulltext=true` must add
     `?include_fulltext=true` explicitly. PR body and CHANGELOG entry call
     this out as the only Group B-induced breaking change.
  2. When `include_fulltext=true` and the payload exceeds `max_chars`,
     fulltext content is truncated. The truncation is signalled via
     `_meta.truncated` and `_meta.next_commands`; callers that need the full
     prose can either pass `max_chars=0` or follow the suggested
     `get_chapter_section` command.

`ResponseMeta` gains two optional fields (`truncated`, `next_commands`); both
default to safe omission and do not break callers that ignore unknown fields.

## Testing Strategy

Add focused unit and route tests in `tests/`:

**#34**
- `tests/unit/test_eutils_client_references.py`: feed a fixture HTML
  containing a recognisable references section; assert
  `_extract_metadata()['references']` is `list[str]` with length > 0.
- Route-level test: a fixture-driven `get_fulltext` response surfaces
  `metadata.references` as a list of at least one entry.

**#39**
- `tests/unit/test_clean_content_unicode.py`: feed a string with
  `<i>BRCA1</i> – Associated` plus a thin space and a narrow
  no-break space; assert the cleaned output contains exactly one ASCII space
  between tokens and no Unicode whitespace characters.
- Property-style sanity: `_clean_content` is idempotent — running it twice
  produces the same string as running it once.

**#29**
- Dockerfile lint / smoke check: assert no `HEALTHCHECK` directive remains.
  Implemented as a simple grep test or, if preferred, a docker-build smoke
  test that inspects the image with `docker inspect --format '{{.Config.Healthcheck}}'`.
- Compose validation: `docker compose -f docker/docker-compose.yml config`
  succeeds with the new `embed` profile and the embed service has
  `healthcheck: disable: true`.

**#36**
- Route test: `GET /chapters/NBK1247/sections/summary` returns 200 with
  `passages == []`, `note` mentions `get_abstract`, and `next_commands[0]`
  is `{"tool": "get_abstract", "arguments": {"pubmed_id": "<id>"}}`.
- Edge: chapter with no `pubmed_id` returns `next_commands == None` (not an
  empty list, not a command with `pubmed_id: None`).

**#38**
- Description / docstring contract test: assert each of the four target
  route descriptions contains the substring `"search_passages"` (chapter
  tools) or `"E-utils"` plus `"normalized"` / `"structured error"` (abstract
  / links tools).
- Usage resource test: assert `USAGE_RESOURCE_MARKDOWN` contains the new
  `search_passages(q, nbk_id=...)` line in the Pipeline section.

**#37**
- Route test: `GET /genereview/BRCA1` default response has `fulltext == None`
  (or absent), `_meta.truncated == False` (or absent), and no `next_commands`.
- Route test: `GET /genereview/BRCA1?include_fulltext=true&max_chars=100`
  returns a truncated payload with `_meta.truncated == True` and
  `_meta.next_commands[0]['tool'] == 'get_chapter_section'`.
- Route test: `GET /genereview/BRCA1?include_fulltext=true&max_chars=0`
  returns a non-truncated payload regardless of size.

All new tests must pass under `make test`. The bundle must pass `make ci-local`
end-to-end before PR is opened.

## Acceptance

- [ ] `get_fulltext("NBK1247")` returns a non-empty `metadata.references`
      list when the upstream Bookshelf HTML contains a references section.
- [ ] `_clean_content()` output for inputs containing U+00A0 / U+2009 /
      U+202F has no Unicode whitespace and no double spaces.
- [ ] `docker run --rm docker-genereview-link genereview-link embed`
      reports container status as `running` (not `unhealthy`).
- [ ] `docker compose --profile embed up genereview-link-embed` starts a
      backfill container with no healthcheck attached.
- [ ] `GET /chapters/NBK1247/sections/summary` returns `note` mentioning
      `get_abstract` and `next_commands[0].tool == "get_abstract"`.
- [ ] `get_chapter_metadata` and `get_chapter_section` route descriptions
      mention `search_passages(q, nbk_id=...)`.
- [ ] `get_abstract` and `get_links` route descriptions name the value-add
      (caching / normalization / structured errors) over raw E-utils.
- [ ] `genereview://usage` Pipeline section includes the in-chapter
      `search_passages(q, nbk_id=...)` escalation step.
- [ ] `GET /genereview/BRCA1` (no params) returns `include_fulltext == False`
      payload by default; PR body documents this default flip as a breaking
      change.
- [ ] `GET /genereview/BRCA1?include_fulltext=true` truncates at the
      `max_chars` cap and surfaces `_meta.truncated` + `_meta.next_commands`
      pointing to `get_chapter_section`.
- [ ] `make ci-local` passes (format, lint, typecheck, tests).

## Ambiguity Report

After one round of design-fork resolution:

| Dimension          | Score | Min  | Status |
|--------------------|-------|------|--------|
| Goal Clarity       | 0.95  | 0.75 | OK     |
| Boundary Clarity   | 0.90  | 0.70 | OK     |
| Constraint Clarity | 0.90  | 0.65 | OK     |
| Acceptance Criteria| 0.85  | 0.70 | OK     |

Ambiguity: 0.09 (gate: <= 0.20)

All four design forks (#37 default flip, #34 scope, #29 scope, #38 breadth)
locked via explicit user choice on 2026-05-23. No dimensions below minimum.

## Next

Plan-phase: `docs/superpowers/plans/2026-05-23-group-b-ergonomics-bundle.md`
with file:line task breakdown, execution order, and commit boundary plan.
