# Production Correctness Bug Batch (Design Spec)

**Date:** 2026-05-30
> Historical record

**Author:** senior MCP engineer (Opus 4.8, in collaboration with project maintainer)
**Source:** GitHub issues #54, #55, #56, #33 (filed from 7-day production log analysis ending 2026-05-30; code at `197dd9d`)
**Branch target:** `fix/prod-correctness-batch` off `main`.

---

## Goal

Restore four broken production contracts in a single low-risk, test-first batch:

1. **#54** — structured JSON logs are emitted as Python `bytes`-reprs (`b'{...}\n'`), breaking all downstream log parsing.
2. **#55** — the multi-worker rate-limiter cannot persist shared state (`Permission denied` on `/tmp/genereview-link`), silently degrading NCBI throttling and spamming 233 warnings / 7 days.
3. **#56** — `get_chapter_metadata` leaks a raw `HTTPStatusError` to MCP clients, discarding the structured `chapter_not_found` error the REST layer already builds.
4. **#33** — multi-row / nested-column table headers are flattened so `len(header) != len(row)`, silently misaligning columns for programmatic consumers.

These are four **independent** fixes. They share only a testing/CI surface, not code. Each ships as its own atomic, TDD-driven commit so any one can be reverted in isolation.

**Out of scope (each gets its own spec -> plan -> implement cycle):** all open enhancement / research / tracker issues — #27 (publish corpus release bundles), #40 (minor-wishes tracker), #43, #44, #45, #46 (search/table/staleness features), #49 (hybrid entity annotation evaluation).

## Success criteria

The batch is done when, with `make ci-local` green (Ruff format + lint, `lint-loc` <= 600 LOC/file, mypy strict on 3.12, tests, 70% coverage floor):

- Every fix below satisfies its issue's acceptance criteria.
- Each fix has at least one regression test that **fails on current `main` and passes after the fix**.
- No module in `genereview_link/`, `server.py`, or `mcp_server.py` grows past the 600-LOC cap or its `.loc-allowlist` ceiling.

---

## Fix 1 — #54: JSON logs emitted as Python `bytes`-reprs

### Root cause (verified)

`genereview_link/logging_config.py:48-50`:

```python
def json_serializer(obj: Any, **kwargs: Any) -> bytes:
    return orjson.dumps(obj, option=orjson.OPT_APPEND_NEWLINE)
```

`orjson.dumps` returns `bytes`. This serializer is wired into `JSONRenderer(serializer=json_serializer)` (L81), whose output becomes the stdlib log record message and is rendered by `logging.Formatter("%(message)s")` (L103). `"%(message)s" % {"message": b"..."}` calls `str(bytes)` -> the literal `b'{...}\n'`. `OPT_APPEND_NEWLINE` adds a newline *inside* the bytes that then appears as a literal `\n` in the quoted repr, while the `StreamHandler` adds the real line terminator.

Production evidence: 945 / 945 JSON log lines over 7 days were in `b'{...}'` form; zero parseable.

### Fix

Make the serializer return `str` and stop appending the newline:

```python
def json_serializer(obj: Any, **kwargs: Any) -> str:
    """Fast JSON serializer using orjson; returns str for the stdlib handler."""
    return orjson.dumps(obj).decode("utf-8")
```

The stdlib `StreamHandler` already terminates each record, so `OPT_APPEND_NEWLINE` must be dropped to avoid a trailing blank line.

### Test

In `tests/test_logging_*` (new `tests/test_logging_json_serializer.py` or extend an existing logging test): render a log event through the production (JSON) processor chain, capture the emitted line, and assert:

- It does **not** start with `b'`.
- `json.loads(line)` succeeds and yields the expected keys (`event`/`level`/`timestamp`/`service`/...).
- The serialized payload contains no literal `\n` substring inside the JSON.

### Risk / blast radius

Single-function change in one file. No public API. Console (dev) path untouched.

---

## Fix 2 — #55: Rate-limit shared-state file unwritable in production

### Root cause (verified)

Two layers compound:

1. **Infra (primary):** `docker/Dockerfile:49-50` creates `/tmp/genereview-link` owned by `app:app`, but `docker/docker-compose.prod.yml:15` mounts a **tmpfs over that same path** (`/tmp/genereview-link:rw,noexec,nosuid,size=512m`). A tmpfs mounted over a directory presents a fresh filesystem owned by `root:root` (default), so the unprivileged `app` user cannot create `rate-limit.state` inside it. `os.makedirs(exist_ok=True)` does **not** fix this — the directory already exists; the problem is ownership/mode of the mount. The deployed overlay (`docker-compose.npm.yml`) must be verified for the same issue.
2. **Code (secondary):** `genereview_link/api/client_manager.py:87-95` warns on **every** failed write and never creates the parent dir or probes writability, so a single misconfiguration emits one WARNING per request (233 / 7 days) while the distributed limiter silently degrades — reads fall back to `0.0`, so cross-worker NCBI throttling effectively stops coordinating.

### Fix

**Code hardening (`DistributedRateLimiter`):**

- On construction (or first use), if a `shared_state_file` is configured: `os.makedirs(os.path.dirname(...), exist_ok=True)` then **probe writability once** (attempt an atomic write/replace of the state file, or a temp file in the same dir).
- If the probe **fails**: log a **single** WARNING naming the path and the errno, then **disable the shared-state path for this process** (treat as `shared_state_file = None`) so the limiter runs in clean in-memory local mode. No per-request warnings; degradation is visible exactly once at startup rather than silent-and-spammy.
- If the probe **succeeds**: behave as today (file-based coordination), and never warn.
- Keep the existing local-timing fallback semantics so the limiter always enforces *some* delay.

**Infra (`docker/`):** ensure every overlay that points `RATE_LIMIT_STATE_FILE` (or `TMPDIR`) at `/tmp/genereview-link` mounts that path writable by the `app` user — set the tmpfs `uid`/`gid`/`mode` (e.g. `mode=1777`, or `uid`/`gid` matching the `app` user) on `docker-compose.prod.yml` and apply the same to `docker-compose.npm.yml` if it sets the state path. Mirror whatever ownership fix landed for the closed bundle-bootstrap issue #31.

### Tests (`tests/` unit, `tmp_path`-based)

- **Writable path:** construct the limiter with a `tmp_path` state file; after `wait_if_needed()`, the file exists and read-back equals the last written timestamp (round-trip).
- **Unwritable path:** point the state file at a directory made read-only (or patch `open` to raise `PermissionError`); assert exactly **one** WARNING is emitted across multiple `wait_if_needed()` calls, the limiter still enforces local timing, and no exception propagates.

### Risk / blast radius

Code change confined to `DistributedRateLimiter`. Compose edits affect deployment only; local/dev (no state file) path is unchanged. NCBI rate-limit floor (0.11s / 0.34s) is preserved by the local fallback.

---

## Fix 3 — #56: `get_chapter_metadata` leaks raw `HTTPStatusError`

### Root cause (verified)

All MCP tools are generated by `FastMCP.from_fastapi(...)` in `genereview_link/server_manager.py:238`. The generated `OpenAPITool.run()` (fastmcp 3.2.4, `server/providers/openapi/components.py:210-248`) calls `response.raise_for_status()` on the internal `http://fastapi` hop and, on a non-2xx, re-raises. The carefully designed `StructuredHTTPException` `detail` (`code` / `recovery_hint` / `next_commands`) built by `chapters.py` is therefore either discarded (raw `HTTPStatusError` reaching the client, as observed 13x / 7 days for non-corpus NBK IDs) or, at best, stringified inconsistently into a `ValueError` message — unlike `search_passages`, whose structured body reaches clients intact.

fastmcp is pinned to **3.2.4** in `uv.lock` and installed via `uv sync --frozen`, so production and local run the same code path; the fix must not depend on FastMCP's internal error formatting staying stable.

### Fix

Pass an `mcp_component_fn` to `from_fastapi` (fastmcp 3.2.4 supports `route_map_fn` and `mcp_component_fn` on `from_fastapi` / `from_openapi`). The hook runs per generated component; for each proxied **tool** we wrap its `run` so that when the underlying FastAPI response is non-2xx, we surface the response's JSON `detail` body as a **clean, uniform** structured MCP tool error — the same shape for `get_chapter_metadata`, `search_passages`, and every other proxied tool. Because we own the formatting, the outcome is identical regardless of FastMCP's internal `ValueError` wording.

The wrapper:

- Recovers the underlying `httpx.Response` from the raised error (e.g. the `HTTPStatusError` in the cause chain) — defensively, so that if FastMCP changes its wrapping we degrade to the original error rather than crashing.
- Parses the JSON body and, when it carries a `detail` with our structured fields, raises a `ToolError` (or returns a structured error result) exposing `code`, `message`, `recovery_hint`, and `next_commands`.
- Falls back to the original error untouched for non-JSON / non-structured bodies.

This logic lives in a small, focused helper (e.g. `genereview_link/mcp/error_passthrough.py`) so `server_manager.py` stays well under the LOC cap and the behavior is unit-testable in isolation.

### Rejected alternatives

- **Return HTTP 200 + error envelope from the routes** so the proxy passes it through as success — breaks REST semantics and HTTP cache/observability for direct REST clients; rejected.
- **Rely on FastMCP's default `ValueError("HTTP error N: ... - {dict}")`** — surfaces a stringified Python dict (not structured), and is version-dependent; rejected.
- **Monkeypatch `OpenAPITool.run` globally** — brittle against library internals; the documented `mcp_component_fn` hook is the supported seam.

### Tests

- **Unit:** the error-passthrough helper, given a synthetic `HTTPStatusError` whose response body is a `StructuredHTTPException` `detail`, produces a structured error exposing `code` / `recovery_hint` / `next_commands`; given a non-JSON body, re-raises the original.
- **Integration (MCP layer):** invoke `get_chapter_metadata` with a non-corpus NBK ID through the constructed MCP server; assert `code == "chapter_not_found"`, `recovery_hint` and `next_commands` present, and **no** raw `HTTPStatusError` / bare traceback. Assert the same structured-error shape is produced for a proxied error and for `search_passages` (consistency criterion).

Follow the repo-local `mcp-tool-change` skill for this fix.

### Risk / blast radius

Adds a wrapping layer at MCP construction; success-path tool responses are unchanged (only non-2xx error surfacing changes). No REST API behavior change.

---

## Fix 4 — #33: Multi-row table headers flattened

### Root cause (verified)

`genereview_link/corpus/tables.py:106-110`:

```python
thead = table.find("thead")
if thead is not None:
    header_row = thead.find("tr")            # only the FIRST <tr>
    if header_row is not None:
        header = [_text_or_empty(th) for th in header_row.findall("th")]
```

Two compounding defects:

1. Only the first `<thead>` `<tr>` is read; nested column groups use an outer row (`<th colspan=2>Risk for Malignancy</th>`) plus an inner row (`<th>BRCA1</th><th>BRCA2</th>`), and the inner row is dropped.
2. The header comprehension does not expand `colspan`, while `parse_rows` (L39) **does** expand colspan/rowspan for body cells — guaranteeing `len(header) < len(rows[i])` whenever a header cell spans columns. `render_table_markdown` (L143) only pads short rows, so the rendered markdown is also malformed.

### Fix

In `extract_table`, build the header from **all** `<thead>` `<tr>`s with colspan/rowspan expanded (reuse / factor the `parse_rows` expansion so header and body share one expansion implementation). Flatten the resulting header grid to a **single leaf header using "Group / Leaf" concatenation** (e.g. `"Risk for Malignancy / BRCA1"`, `"Risk for Malignancy / BRCA2"`), guaranteeing `len(header) == len(row)` for every body row. **No schema change** to `TableResponse` (`header: list[str]`, `rows: list[list[str]]`) — the maintainer chose the flatten approach over a new `header_groups` field.

Additional polish from the issue:

- Strip trailing footnote-marker artifacts on header cells (e.g. `"Risk for Malignancy  1"`).
- Tighten `render_table_markdown` to validate the width invariant (header width == each row width) rather than only padding short rows.

### Tests (`tests/unit/test_corpus_tables.py`)

- Add a fixture with a nested-header table (NBK1247 Table 2 shape, or a faithful synthetic equivalent) under `tests/fixtures/`.
- Assert `len(header) == len(row)` for every row; assert the concatenated group/leaf labels are correct; assert the test **fails on current code** and passes after.
- Assert single-row-header tables are unchanged (no regression in existing `test_corpus_tables.py`).

Follow the repo-local `ncbi-scraper-change` skill (refresh/extend fixtures) for this fix.

### Risk / blast radius

Confined to `corpus/tables.py` (currently ~163 LOC; the fix keeps it well under the cap). Output shape (`header`, `rows`) is unchanged; only correctness improves. Single-header tables are unaffected.

---

## Sequencing and method

1. TDD per fix: write the failing regression test first, then the minimal fix, confirm green.
2. One atomic commit per fix (`fix(logging): ...`, `fix(rate-limit): ...` + `fix(docker): ...`, `fix(mcp): ...`, `fix(tables): ...`), referencing the issue number.
3. Recommended order — independent, but cheapest-to-riskiest: **#54 -> #33 -> #55 -> #56**.
4. `make ci-local` green before handoff; verify LOC budget unaffected.

## Verification matrix

| Issue | Acceptance criterion | Verifying test |
| --- | --- | --- |
| #54 | Log lines are valid UTF-8 JSON, no `b'...'`, no literal `\n` | logging serializer round-trip test |
| #55 | No per-request `Failed to write shared state` spam; state persists when writable; surfaced once when not | rate-limiter writable + unwritable tests |
| #56 | `get_chapter_metadata` on non-corpus NBK ID returns structured `chapter_not_found`; consistent with `search_passages` | MCP integration test + helper unit test |
| #33 | `get_table` columns match every row; new test fails pre-fix, passes post-fix; no regression | nested-header fixture test |
