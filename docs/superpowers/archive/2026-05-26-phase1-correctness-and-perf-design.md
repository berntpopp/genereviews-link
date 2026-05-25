# Phase 1 Correctness And Performance Design

**Date:** 2026-05-26
**Status:** Completed in PR #53, merged to `main` as
`4496721b5bbe58106220b92baf20e6fd6aa85da6`.
**Findings:** #1, #2, #4, #11, #12, #19 (from
`.planning/2026-05-25-senior-engineering-review.md`), plus one
enabling refactor (extract `server_lifecycle.py` from `server_manager.py`).

**#18 dropped during code review:** the senior eng review claimed
`/metrics` was exposed as an MCP tool, but `server_manager.py:427`
already sets `include_in_schema=False`, which removes the route from
the OpenAPI schema `FastMCP.from_fastapi` walks. Verified by
introspection; no fix required.

**Module split prerequisite (added after code review):**
`server_manager.py` is at its 618 LOC ceiling (and past the AGENTS.md
soft-trigger of 500). #4 (tar hardening) and #2 (lifecycle helpers)
together add ~30–100 LOC; they cannot land in that file without
growing it. A pure-refactor commit extracts `_bootstrap`,
`_bundle_bootstrap_paths`, and the new `_sha256_stream` /
`_initialize_state` / `_teardown_state` into
`genereview_link/server_lifecycle.py`. `UnifiedServerManager` stays
in `server_manager.py` and imports from the new module. Both files
end well under 600 LOC; the allowlist entry for `server_manager.py`
shrinks accordingly; no allowlist entry is needed for the new module.

**Scope:** First remediation phase from the 2026-05-25 senior engineering
review. Highest-leverage, lowest-risk correctness and performance items;
no schema changes, no public API changes, no defusedxml-affecting XML
touches.

This is the natural follow-on to PR #52 (Group B ergonomics). Group B
closed LLM-ergonomics polish; Phase 1 closes the latent correctness bugs
and cheap performance wins that block confidence in a pre-1.0 cut.

## Goal

Make the live system materially more correct and faster under realistic
production traffic patterns, in a single review-sized PR:

- fix the cache that silently never expires (#1 wires through the TTL
  that is already read from settings);
- fix the STDIO MCP transport so `/passages/*` tools stop returning 503
  (#2 extracts the lifespan body into a reusable helper called from both
  start paths);
- harden the bundle bootstrap against malicious tarballs and verify
  manifest hashes before extraction (#4);
- eliminate a per-query Postgres round-trip on every retrieval path
  (#11 moves `set search_path` into `_init_conn`);
- production-tune the asyncpg pool with command timeout, prepared-statement
  cache control, idle-connection lifetime tunability, and a higher default
  size (#12);
- delete the dead `mcp_custom_names` identity-mapped dict that misleads
  readers into thinking renames are happening (#19).

## Problems Addressed

**#1 CACHE_TTL_HOURS is read but never applied.**
`services/genereview_service.py:43-52` constructs `self.cache_ttl =
timedelta(hours=settings.CACHE_TTL_HOURS)` and then never passes the
value into the three `alru_cache` constructions on lines 46, 47, 50.
`async_lru==2.3.0` supports a `ttl` kwarg; today the kwarg is silently
dropped and scraped GeneReview content lives in-process until LRU
eviction or restart. Operators who set `CACHE_TTL_HOURS=1` to limit
staleness see no effect.

**#2 STDIO MCP transport runs without the lifespan body.**
`server_manager.py:580-592` (`start_stdio_server`) creates the FastAPI
app, manually initialises the client and service managers, then runs
`mcp.run_async(transport="stdio")`. The `lifespan` method body
(lines 184-289) — which owns the Postgres pool, the repository, the
embedder, the gene_index, the cached corpus_version, and the release
watcher — never executes on this path. An MCP client connecting over
stdio gets a server where `search_passages`, `get_passage`,
`get_chapter_metadata`, and `get_chapter_section` all return 503
because `app.state.repository is None`. This silently breaks the
canonical pipeline documented in the MCP `instructions` string.

**#4 tarfile.extractall before manifest verify + no filter + jobs=None.**
`server_manager._bootstrap` lines 118-128 extract the entire bundle to
disk before checking any of the per-file SHAs that `manifest.json`
records. The outer `.sha256` of the tarball guards the blob in transit,
not the contents against on-disk corruption or a forged manifest. The
extract call also omits `filter="data"`, which Python 3.12 warns about
and Python 3.14 makes mandatory — without it, a hostile tar member with
`../` path components or symlinks can write outside `extract_dir`
(CVE-2007-4559 family). Finally, `jobs=os.cpu_count()` on exotic
systems where `cpu_count()` returns `None` raises a `TypeError` deep in
`pg_restore`.

**#11 `set search_path` runs per query.**
`retrieval/repository.py` issues `await conn.execute("set search_path to
genereview, public")` on lines 342, 437, 454, 468, 488, 512, 596, 662,
746, 780, 805, 845, 863 — 13 sites, every read path. Each call is an
extra network round-trip. The ingest path correctly uses `SET LOCAL
search_path` inside a transaction (`corpus/pipeline.py:237`), which
wins over the connection default — so moving the default into
`_init_conn` is safe.

**#12 asyncpg pool is not production-tuned.**
`db/pool.create_pool` constructs `asyncpg.create_pool(dsn, min_size=2,
max_size=10, init=_init_conn)`. Two operationally-significant kwargs
are missing entirely; one is locked at the asyncpg default with no
operator override; the pool size is tight for unified mode:

- `command_timeout` (no current value) — a pathological query can
  currently pin a connection forever.
- `statement_cache_size` (no current value; asyncpg default `100`) —
  asyncpg's prepared-statement cache breaks under PgBouncer
  transaction-pooling mode. Operators behind PgBouncer txn-mode have
  no way to set this to `0` without forking the pool factory.
- `max_inactive_connection_lifetime` (asyncpg default `300.0`) — the
  asyncpg default already kills idle connections at 5 minutes, which
  protects against most cloud-Postgres firewall timeouts. Aggressive
  edge cases (RDS Proxy with custom IdleClientTimeout under 5 min, or
  PgBouncer with `server_idle_timeout < 300`) need a lower value;
  operators currently cannot tune it.
- `max_size=10` is also tight for unified mode (HTTP + MCP +
  parallel-retrieval, where a single search request can acquire
  multiple connections via lexical / dense / chapter-metadata fanout).

**#19 `mcp_custom_names` is a dead identity-mapped dict.**
`server_manager.create_mcp_server` lines 435-445 declare:

```python
mcp_custom_names = {
    "get_genereview_summary": "get_genereview_summary",
    "search_genereviews": "search_genereviews",
    ...
}
```

Every key maps to itself — that is the FastMCP default. The dict is
passed as `mcp_names=mcp_custom_names` and accomplishes nothing, while
tricking readers into believing a custom-naming layer exists.

## Non-Goals

- **No corpus-swap invalidation for #1.** `GeneReviewService.get_genereview`
  scrapes NCBI Bookshelf live; corpus version changes do not make its
  cache entries stale. The corpus-derived staleness (`app.state.corpus_version`,
  `app.state.gene_index`) is finding #6 and ships in a later phase with
  a `RepositoryRefresher`.
- **No schema migration** (Phase 1 touches no SQL DDL).
- **No public API additions or breaking changes.** Operator-visible
  defaults change only as named in the Contract Changes section.
- **No `EutilsClient` or `passages.py` split** — those are findings #16
  and #23, tracked in `.loc-allowlist` and a separate decomposition
  backlog.
- **No release-watcher implementation or removal** (finding #5).
- **No DistributedRateLimiter rework** (finding #9).
- **No `raise Exception` → typed-exception migration** (finding #10).
- **No documentation rewrite** beyond a short `AGENTS.md` note for
  `DATABASE_STATEMENT_CACHE_SIZE` under "Postgres connection".

## Design

### #1 Cache TTL wire-up

`genereview_link/services/genereview_service.py:43-52` becomes:

```python
ttl_seconds = settings.CACHE_TTL_HOURS * 3600
self.get_genereview = alru_cache(
    maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
)(self._get_genereview_impl)
self.get_genereview_comprehensive = alru_cache(
    maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
)(self._get_genereview_comprehensive_cached_impl)
self.get_genereview_comprehensive_indexed = alru_cache(
    maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
)(self._get_genereview_comprehensive_indexed_impl)
```

`self.cache_ttl` (line 43) is deleted as a dead attribute. The three
cached methods are functionally identical for cache shape; all three
get the same TTL.

### #2 STDIO lifespan extraction

The lifecycle helpers land in the new `genereview_link/server_lifecycle.py`
module (see "Module split prerequisite" above). Two module-level
coroutines are added there:

```python
async def _initialize_state(app: FastAPI) -> None:
    """Run bootstrap, attach pool/repository/embedder/gene_index/
    corpus_version/scheduler to app.state."""
    ...

async def _teardown_state(app: FastAPI) -> None:
    """Symmetric teardown for everything _initialize_state attached."""
    ...
```

The body of `UnifiedServerManager.lifespan` (lines 184-289) moves
verbatim into `_initialize_state`, with two adjustments:

- `pool` is no longer a local-scope variable kept for cleanup; it lives
  exclusively on `app.state.pool` (already true today — the local is a
  side-effect-free alias). `_teardown_state` reads from `app.state.pool`.
- `scheduler` (currently a lifespan-scope local on line 278) is stashed
  on `app.state.scheduler`. `_teardown_state` reads from there with
  `getattr(app.state, "scheduler", None)` so a partial init does not
  crash teardown.

`UnifiedServerManager.lifespan` becomes:

```python
@asynccontextmanager
async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
    await _initialize_state(app)
    try:
        yield
    finally:
        await _teardown_state(app)
```

`UnifiedServerManager.start_stdio_server` becomes:

```python
async def start_stdio_server(self, config: ServerConfig) -> None:
    self._current_transport = "stdio"
    logger.info("Starting STDIO MCP server...")
    self.app = self.create_fastapi_app(config)
    await _initialize_state(self.app)
    try:
        self.mcp = await self.create_mcp_server(self.app, config)
        await self.mcp.run_async(transport="stdio")
    finally:
        await _teardown_state(self.app)
```

The manual `client_manager` / `service_manager` initialisation lines
584-589 are removed — `_initialize_state` already does this.

### #4 tarfile hardening + jobs None

`server_lifecycle._bootstrap` (moved out of `server_manager.py` by the
preceding refactor commit) lines around the bundle-restore block are
restructured so that (a) checksum verification happens against
in-tarball bytes before any file is written to disk, (b) only members
listed in `manifest.json` (plus the manifest itself) are extracted —
unexpected, duplicate, or non-checksummed extras are rejected,
(c) `filter="data"` is applied:

```python
import hashlib
...

def _sha256_stream(fh) -> str:
    hasher = hashlib.sha256()
    while True:
        chunk = fh.read(65536)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()

with tf_mod.open(tmp, "r:gz") as tar:
    # 1. Read manifest from in-tarball bytes.
    manifest_member = tar.getmember("manifest.json")
    manifest_fh = tar.extractfile(manifest_member)
    if manifest_fh is None:
        raise RuntimeError("manifest.json missing or unreadable from bundle")
    manifest = json.loads(manifest_fh.read())
    expected_members = {"manifest.json", *manifest["checksums"].keys()}

    # 2. Reject unexpected members (extras a malicious tar could smuggle
    #    in under safe paths) and duplicate names.
    seen: set[str] = set()
    for member in tar.getmembers():
        if member.name in seen:
            raise RuntimeError(f"duplicate tar member: {member.name}")
        seen.add(member.name)
        if member.name not in expected_members:
            raise RuntimeError(
                f"unexpected tar member not listed in manifest: {member.name}"
            )

    # 3. Verify every manifest-listed member against in-tarball bytes.
    for relpath, expected in manifest["checksums"].items():
        member = tar.getmember(relpath)
        fh = tar.extractfile(member)
        if fh is None:
            raise RuntimeError(f"manifest references missing member {relpath}")
        if _sha256_stream(fh) != expected:
            raise RuntimeError(f"manifest checksum mismatch on {relpath}")

    # 4. Extract only the verified members. Each call applies
    #    filter="data" so unsafe paths/symlinks are rejected even
    #    though we already restricted membership.
    for name in expected_members:
        tar.extract(tar.getmember(name), path=str(extract_dir), filter="data")

await pg_restore(
    extract_dir / "corpus.dump",
    database_url=settings.DATABASE_URL,
    jobs=os.cpu_count() or 2,
)
```

The tar handle is reused across all four passes; opening from a path
produces a seekable stream for `r:gz`. 64 KiB streaming chunks bound
peak memory at well under 1 MiB regardless of dump size. The expected-set
check defends against safe-pathed extras (e.g. `evil.py` inside the
tarball that is not listed in `manifest.json` and therefore not
checksummed). `filter="data"` is the second line of defence per
PEP 706 — absolute paths and outside-directory symlinks are rejected.
`os.cpu_count() or 2` defends against systems where `cpu_count()`
returns `None`. The `_sha256_stream` helper is a module-private function
to keep `_bootstrap`'s body flat and to sidestep Ruff `B023`
loop-captured-closure noise.

### #11 `set search_path` via `server_settings` (not `init=`)

**Why not `init=`:** asyncpg's pool runs `Connection.reset` on every
release. Per the asyncpg docstring: *"all session configuration
variables are reset to their default values."* Any `set search_path`
issued from `init=` therefore survives only the first acquire — the
next release wipes it, and every subsequent acquire sees the
session-default `"$user, public"`. The same applies to a `setup=`
callback (it would re-run per acquire, paying a round-trip per
acquire and defeating the perf win we want).

**Correct mechanism:** pass `server_settings={"search_path":
"genereview, public"}` to `asyncpg.create_pool` (forwarded to
`connect`). This sends `search_path` as a Postgres startup parameter,
which becomes the connection's **session default** — RESET ALL reverts
to *this* value, not to `"$user, public"`. Zero per-acquire round-trip;
survives release/reset; survives `SET LOCAL` (which still wins inside
txn and reverts on txn end).

`genereview_link/db/pool.create_pool` becomes:

```python
return await asyncpg.create_pool(
    dsn=s.DATABASE_URL,
    min_size=s.DATABASE_POOL_MIN_SIZE,
    max_size=s.DATABASE_POOL_MAX_SIZE,
    max_inactive_connection_lifetime=s.DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S,
    command_timeout=s.DATABASE_COMMAND_TIMEOUT_S,
    statement_cache_size=s.DATABASE_STATEMENT_CACHE_SIZE,
    server_settings={"search_path": "genereview, public"},
    init=_init_conn,
)
```

`_init_conn` keeps only the pgvector codec registration — no
`set search_path` call. The 13 per-call `set search_path` lines in
`retrieval/repository.py` (342, 437, 454, 468, 488, 512, 596, 662,
746, 780, 805, 845, 863) are deleted. The `corpus/pipeline.py:237`
`SET LOCAL search_path` continues to win over the connection default
inside its txn and reverts on txn end, restoring the session default.

**Integration-fixture update (required):**
`tests/integration/conftest.py:78` currently constructs its pool
without going through `db.pool.create_pool`:

```python
pool = await asyncpg.create_pool(
    url, min_size=1, max_size=4, init=pgvector.asyncpg.register_vector
)
```

After the per-query `set search_path` calls are deleted from
`repository.py`, this fixture would leave the session-default
`search_path` as `"$user, public"`, and every integration test that
exercises a repository method would query the wrong schema. The
fixture must be updated in the same commit (#11) to add the
`server_settings` startup parameter:

```python
from genereview_link.db.pool import _init_conn

pool = await asyncpg.create_pool(
    url,
    min_size=1,
    max_size=4,
    server_settings={"search_path": "genereview, public"},
    init=_init_conn,
)
```

This couples the integration fixture to the production pool's
session-default contract, which is the correct shape.

**Verification (mandatory):** the new unit test exercises the
acquire/release/acquire cycle against a real test Postgres (when
`GENEREVIEW_TEST_DATABASE_URL` is set; skipped otherwise) to prove
`search_path` survives the reset:

1. Acquire conn A; assert `show search_path` is `"genereview, public"`.
2. Release A.
3. Acquire conn B (may be the same physical conn after reset);
   assert `show search_path` is **still** `"genereview, public"`.

This catches future regressions where someone moves the setting back
into `init=` or `setup=` without realising RESET ALL wipes it.

### #12 Pool tuning

`genereview_link/config.Settings` gains three fields and one default
change:

```python
DATABASE_POOL_MIN_SIZE: int = 2
DATABASE_POOL_MAX_SIZE: int = 20  # was 10
DATABASE_ACQUIRE_TIMEOUT_S: float = 5.0
DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S: float = 300.0  # new
DATABASE_COMMAND_TIMEOUT_S: float | None = None  # new (None = asyncpg default)
DATABASE_STATEMENT_CACHE_SIZE: int = 100  # new (asyncpg default; 0 disables for PgBouncer txn mode)
```

`genereview_link/db/pool.create_pool` threads the three new settings:

```python
return await asyncpg.create_pool(
    dsn=s.DATABASE_URL,
    min_size=s.DATABASE_POOL_MIN_SIZE,
    max_size=s.DATABASE_POOL_MAX_SIZE,
    max_inactive_connection_lifetime=s.DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S,
    command_timeout=s.DATABASE_COMMAND_TIMEOUT_S,
    statement_cache_size=s.DATABASE_STATEMENT_CACHE_SIZE,
    init=_init_conn,
)
```

`AGENTS.md` gains a short paragraph under a new "Postgres connection"
subsection noting `DATABASE_STATEMENT_CACHE_SIZE=0` as the required
setting under PgBouncer transaction-pooling mode.

### #19 Delete `mcp_custom_names`

`server_manager.create_mcp_server` lines 435-445 (the dict literal) are
deleted. The `mcp_names=mcp_custom_names` kwarg on the
`FastMCP.from_fastapi` call (line 479) is removed. FastMCP's default
naming preserves the existing tool names.

## Test Plan

All new tests live under `tests/unit/` to match the existing convention
(`tests/test_*.py` files at the root are route-level / integration-flavoured;
`tests/unit/test_*.py` are pure unit tests). Each finding gets its own
test file (or extends one created earlier in the PR).

### #1 Cache TTL — `tests/unit/test_genereview_service_cache_ttl.py`

Two tests, both deterministic. `async_lru` upstream tests cover the
TTL semantics; this file verifies *our wiring* of it.

1. **`test_ttl_kwarg_is_passed_to_alru_cache`** — patch
   `genereview_link.services.genereview_service.alru_cache` to a
   `MagicMock` whose return value is a passthrough decorator (`lambda
   fn: fn`); construct `GeneReviewService()` with
   `settings.CACHE_TTL_HOURS=2`; assert the patched `alru_cache` was
   called three times, each with kwargs `maxsize=settings.CACHE_SIZE`
   and `ttl=7200`. This is the only test the executor needs to lock the
   wiring contract — it tests the *one line we wrote*. Verified note:
   `cache_parameters()` does not expose `ttl` in `async_lru==2.3.0` and
   the TTL is reachable only via the name-mangled
   `_LRUCacheWrapper__ttl` private attribute, so the spy pattern is
   strictly better than reaching into wrapper internals.
2. **`test_cache_hits_underlying_client_only_once_within_ttl`** —
   construct `GeneReviewService(client=mock_client)` where
   `mock_client` is a `MagicMock` whose three relevant methods
   (`search_genereview_pmid`, `get_book_url_from_pmid`,
   `scrape_genereview_book`) are `AsyncMock`s returning canned values;
   await `service.get_genereview("BRCA1")` twice; assert
   `mock_client.search_genereview_pmid.await_count == 1`. This catches
   future regressions where the alru_cache wrapper is silently removed
   (the body would call the client every time). It does **not** test
   TTL expiry — that is async_lru's responsibility, verified by Test 1
   showing the TTL kwarg reaches the library.

Replacing `_get_genereview_impl` after construction does **not** work:
`alru_cache` captures the bound method at construction time, so
monkeypatching the attribute later has no effect on the cached
wrapper. The test must inject the mock through the constructor's
`client=` parameter, exactly as `GeneReviewService` already supports.

### #2 STDIO lifespan — `tests/unit/test_server_manager_lifespan.py`

Two tests, both deterministic, no Postgres, no network:

1. **`test_initialize_state_with_empty_database_url`** — monkeypatch
   `settings.DATABASE_URL = ""`; build a fresh `FastAPI()`; call
   `await _initialize_state(app)`; assert `app.state.pool is None`,
   `app.state.repository is None`, `app.state.corpus_version is None`,
   `app.state.gene_index is None`, `isinstance(app.state.embedder,
   FakeEmbeddingProvider)`, `getattr(app.state, "scheduler", None) is
   None`. Then call `await _teardown_state(app)` and assert it completes
   without raising.
2. **`test_start_stdio_server_invokes_initialize_and_teardown`** —
   monkeypatch the module-level `_initialize_state` and `_teardown_state`
   to `AsyncMock` instances; monkeypatch `UnifiedServerManager.create_mcp_server`
   to return a stub whose `run_async` is an `AsyncMock` resolving
   immediately; call `await UnifiedServerManager().start_stdio_server(
   ServerConfig(transport="stdio"))`; assert `_initialize_state.await_count
   == 1`, `run_async.await_count == 1`, `_teardown_state.await_count == 1`.
   Add a third variant that makes `run_async` raise and asserts
   `_teardown_state` still ran (covers the `finally` branch).

### #4 tarfile hardening — `tests/unit/test_bootstrap_tarfile_security.py`

Five tests using ad-hoc tarballs built in `tmp_path`. Two of them are
defenses-in-depth that exercise distinct guard layers:

1. **`test_bootstrap_rejects_forged_manifest_before_extracting`** —
   build a tarball containing a valid `manifest.json` listing a wrong
   SHA for `corpus.dump`; assert `_bootstrap` raises `RuntimeError`
   matching `"manifest checksum mismatch"`; assert `extract_dir` does
   not contain `corpus.dump`.
2. **`test_bootstrap_rejects_unexpected_member`** — build a tarball
   with a valid `manifest.json`, a valid `corpus.dump` matching the
   manifest checksum, **and** an extra `evil.txt` not listed in the
   manifest; assert `_bootstrap` raises `RuntimeError` matching
   `"unexpected tar member not listed in manifest"`; assert
   `extract_dir` does not contain `evil.txt`.
3. **`test_bootstrap_rejects_duplicate_member`** — build a tarball
   where `corpus.dump` appears twice (second `addfile` for the same
   name) with valid manifest+checksum for that name; assert
   `_bootstrap` raises `RuntimeError` matching `"duplicate tar member"`.
4. **`test_bootstrap_filter_data_blocks_listed_unsafe_path`** — build
   a tarball with `manifest.json` whose `checksums` lists a member
   named `"corpus.dump"` (valid SHA) **plus** an absolute-path member
   like `"/tmp/evil.txt"` (valid SHA, also listed in manifest). The
   membership check accepts the entry because it IS in the manifest;
   only `filter="data"` rejects it during extract. Assert `_bootstrap`
   raises (per PEP 706, `filter="data"` raises `tarfile.AbsolutePathError`
   or `tarfile.OutsideDestinationError`); assert no file appears at
   `/tmp/evil.txt`. This test *forces* `filter="data"` to be the
   protection — an implementation that omitted the kwarg would
   regress.
5. **`test_bootstrap_handles_cpu_count_none`** — monkeypatch
   `os.cpu_count` to return `None`; patch `pg_restore` to an `AsyncMock`
   (it is awaited in `_bootstrap`); build a minimal valid tarball; call
   `_bootstrap`; assert `pg_restore.await_args.kwargs["jobs"] == 2`.

### #11 search_path via server_settings — two test files

**Unit test:** `tests/unit/test_pool_search_path.py`

1. **`test_create_pool_passes_search_path_server_setting`** — pure-mock
   test: patch `asyncpg.create_pool` to an `AsyncMock`; call
   `await create_pool()`; assert the captured kwargs include
   `server_settings={"search_path": "genereview, public"}`. This is
   the wiring test — it verifies the one line we wrote.
2. **`test_repository_module_has_no_set_search_path_calls`** —
   grep-based unit test: read
   `genereview_link/retrieval/repository.py`, assert the substring
   `'set search_path'` does not appear. Regression guard.

**Integration test:** `tests/integration/test_pool_search_path_survives_reset.py`

3. **`test_search_path_survives_pool_release_and_reacquire`** —
   requires `GENEREVIEW_TEST_DATABASE_URL` (skipped otherwise; uses
   the existing `pool` fixture from `tests/integration/conftest.py`).
   Acquire conn A, assert `await conn.fetchval("show search_path")`
   returns `"genereview, public"`. Release A. Acquire conn B (may be
   the same physical connection after asyncpg's RESET ALL), assert
   `show search_path` is **still** `"genereview, public"`. This is
   the test that catches the bug Codex review identified — an
   implementation that uses `init=` instead of `server_settings`
   passes the unit test but fails this one.

Integration coverage of every other repository method is provided by
the existing `tests/integration/` suite — every repository test stops
working if `search_path` is wrong, so the suite is its own regression
net.

### #12 Pool tuning — extend `tests/unit/test_pool_search_path.py`

Two tests:

1. **`test_create_pool_passes_tuning_kwargs`** — patch
   `asyncpg.create_pool` to an `AsyncMock` (it is awaited inside
   `create_pool`); configure settings with custom non-default values
   for the three new fields; `await create_pool()`; assert the mock
   was called with `command_timeout`, `statement_cache_size`, and
   `max_inactive_connection_lifetime` matching the configured values.
2. **`test_default_pool_max_size_is_20`** — `Settings()` with no env
   overrides; assert `settings.DATABASE_POOL_MAX_SIZE == 20`.

Also extend `tests/test_config_database.py` with one assertion that the
three new fields exist on `Settings()` with their documented defaults
(`300.0`, `None`, `100`).

### #19 MCP tool surface — `tests/unit/test_mcp_tool_surface.py`

One test:

1. **`test_mcp_tools_keep_canonical_names_after_dict_removal`** —
   build the MCP server in-process (mirror the
   `_build_app_with_state` pattern from
   `tests/test_mcp_tool_dispatch.py`); call `await mcp.get_tools()` (or
   the equivalent FastMCP introspection API confirmed at execution
   time); assert every name in the canonical pipeline
   (`search_passages`, `get_passage`, `get_chapter_metadata`,
   `get_chapter_section`, `get_table`, `get_passages_batch`,
   `get_genereview_summary`, `search_genereviews`, `get_abstract`,
   `get_links`, `get_fulltext`, `get_license`) appears in the tool
   set. Regression guard against future identity-mapped dicts and
   against any unintended rename when `mcp_names=` is removed.

### Gate

`make ci-local` is the merge gate. It runs `format-check`, `lint-ci`,
`lint-loc`, `typecheck-fast`, and `test-unit`. The `lint-loc` step is
the LOC budget guard described under Risks.

## Risks

1. **asyncpg RESET ALL wipes session settings (#11).** asyncpg's pool
   runs `Connection.reset` on every release, which resets all session
   configuration variables. This is why `init=` (or `setup=`) is the
   wrong mechanism for `search_path` — the setting survives only the
   first acquire, then gets wiped. *Mitigation:* the design uses
   `server_settings={"search_path": "genereview, public"}` which goes
   into the connection startup parameters and survives RESET ALL.
   The new integration test `test_search_path_survives_pool_release_and_reacquire`
   exercises the acquire/release/acquire cycle to catch any future
   regression that moves the setting back to `init=`.
2. **`SET LOCAL search_path` interaction (#11).** Connection-default
   `search_path` is overridden inside any txn that issues `SET LOCAL`,
   then restored on txn end. Verified in `corpus/pipeline.py:237`.
   *Mitigation:* the existing ingest test suite exercises the txn path;
   #11's commit runs it as a regression guard.
3. **#2 scheduler ownership move.** Stashing `scheduler` on
   `app.state` changes its lifetime from "local in lifespan" to
   "attribute on app." A partial init that raises before `scheduler =
   AsyncIOScheduler()` would leave `app.state.scheduler` unset.
   *Mitigation:* `_teardown_state` uses `getattr(app.state, "scheduler",
   None)` and short-circuits on `None`; covered by Test #2.1 above.
4. **#4 manifest re-verify reads the tarball twice.** The
   verification pass streams every member through SHA-256 before
   extraction starts, which doubles I/O on bootstrap. Acceptable at
   one-time-per-restart cost. *Mitigation:* 64 KiB streaming chunks
   bound peak memory; the trade-off is "verify before extract" which
   is the entire point of the manifest.
5. **`server_manager.py` LOC ceiling.** Current allowlist entry is
   `genereview_link/server_manager.py:618`. #4 (tar hardening) and #2
   (lifecycle helpers) together add ~30–100 net LOC; that would push
   the file well past 618. *Mitigation:* commit #5 is a pure refactor
   that moves `_bootstrap`, `_bundle_bootstrap_paths`, and the new
   `_sha256_stream` / `_initialize_state` / `_teardown_state` into
   `genereview_link/server_lifecycle.py`. After the split,
   `server_manager.py` should land near 400 LOC and the allowlist entry
   is lowered to the new actual; the new module is well under 600 LOC
   and needs no allowlist entry. The subsequent commits (#4 and #2)
   then add their logic to `server_lifecycle.py`, which still ends
   under 600. `make lint-loc` is the gate at every commit.
6. **#1 cache TTL test wiring.** The TTL keyword is verified by spying
   on `alru_cache` itself during construction (see Test #1.1), not by
   reaching into `_LRUCacheWrapper__ttl` or relying on
   `cache_parameters()` — neither is supported by `async_lru==2.3.0`'s
   public API.
7. **Operator-visible default `DATABASE_POOL_MAX_SIZE` rises 10 → 20.**
   Existing deployments with `DATABASE_POOL_MAX_SIZE=10` in env keep
   their override. *Mitigation:* called out in the PR body under
   "Operator-visible defaults." A Postgres `max_connections` of 100 (a
   common cloud default) still supports 5 workers × 20 connections.
8. **`DATABASE_STATEMENT_CACHE_SIZE` default `100` matches asyncpg's
   own default.** Zero behaviour change unless operator opts out.
   *Mitigation:* documented in `AGENTS.md` under "Postgres connection."

## Contract Changes

None public-facing. The following are silent unless operators set new env vars:

| Change | Default | Effect on existing deployments |
|---|---|---|
| `_initialize_state` / `_teardown_state` helpers added to `server_manager.py` | n/a | Module-private (underscore prefix). |
| STDIO MCP transport now serves `/passages/*` tools | n/a | Bug-fix: previously returned 503. |
| `DATABASE_POOL_MAX_SIZE` default | `10 → 20` | Only affects deployments that never set the var. |
| `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S` | `300.0` (new) | Matches asyncpg default; zero behaviour change unless operator tunes. |
| `DATABASE_COMMAND_TIMEOUT_S` | `None` (new) | asyncpg default = no timeout; zero change unless set. |
| `DATABASE_STATEMENT_CACHE_SIZE` | `100` (new) | Matches asyncpg default; zero behaviour change unless operator sets to 0 for PgBouncer txn mode. |
| `mcp_custom_names` deleted | n/a | Tool names unchanged (dict was identity-mapped). |
| `genereview_service.alru_cache` entries now expire after `CACHE_TTL_HOURS` | n/a | Bug-fix: previously never expired. |
| `_bootstrap` rejects tarballs with unsafe members, unexpected members, or forged manifests | n/a | New defence; legitimate bundles unaffected. |

## Branch And Commit Plan

- **Branch:** `feat/phase1-correctness-and-perf`
- **Commits (in this order):**
  1. `refactor(mcp): drop dead mcp_custom_names identity-mapped dict (#19)`
  2. `fix(services): wire CACHE_TTL_HOURS into alru_cache instances (#1)`
  3. `perf(db): use server_settings for search_path connection-default (#11)`
  4. `feat(db): production-tune asyncpg pool kwargs and defaults (#12)`
  5. `refactor(server): extract server_lifecycle.py from server_manager.py`
  6. `fix(corpus): verify manifest hashes and reject extras before extract (#4)`
  7. `fix(mcp): extract lifespan body into _initialize_state for STDIO transport (#2)`
- **Allowlist update:** commit #5 (the pure refactor that extracts
  `server_lifecycle.py`) is responsible for the `wc -l` drop on
  `server_manager.py`. That commit runs
  `wc -l genereview_link/server_manager.py`, updates `.loc-allowlist`
  to the new measured value in the same commit, and `make lint-loc`
  is the gate. The new `server_lifecycle.py` module is well under
  600 LOC and does not need an allowlist entry.
- **PR body:** closing-list refs for #1, #2, #4, #11, #12, #19;
  "Operator-visible defaults" callout for `DATABASE_POOL_MAX_SIZE`
  bump; an explicit "#18 verified obsolete during review — already
  excluded by `include_in_schema=False`" note; smoke-test command
  uses the actual CLI shape: `uv run genereview-link serve
  --transport stdio` (not `mcp serve`); test-plan checkboxes mirror
  Group B style.
