# Precomputed Corpus Bundle Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish complete GeneReviews corpus bundles locally on the RTX 5090, then let Docker restore those bundles from GitHub Releases instead of ingesting/backfilling on first boot.

**Architecture:** Add producer-side bundle metadata, validation, and publish commands around the existing ingest/embed/bundle primitives. Replace the existing GitHub Actions CPU corpus builder with a release-asset verifier. Keep Docker as a consumer: `BUNDLE_URL=latest` or a pinned release URL downloads and restores the GitHub Release asset.

**Tech Stack:** Python 3.12, Typer, asyncpg, PostgreSQL 18 + pgvector 0.8.2, sentence-transformers/BGE-small, local CUDA via PyTorch, GitHub CLI, GitHub Actions, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-05-12-precomputed-corpus-bundle-design.md`

---

## File Map

**Create:**

- `genereview_link/corpus/bundle_metadata.py` - release-id validation, asset naming, migration/count/HNSW metadata collection.
- `genereview_link/corpus/bundle_validation.py` - reusable bundle readiness validation and manifest checksum verification.
- `tests/unit/test_corpus_bundle_metadata.py` - release-id and asset naming tests.
- `tests/unit/test_corpus_bundle_validation.py` - validation result tests without a live database.
- `.github/workflows/verify-corpus-bundle.yml` - manually verifies a GitHub Release asset.
- `scripts/verify_torch_cuda.py` - small local CUDA preflight used by docs and release checklist.

**Modify:**

- `genereview_link/corpus/bundle.py` - extend `BundleManifest`.
- `genereview_link/cli.py` - add `bundle validate`, improve `bundle build`, add `bundle publish-local`.
- `genereview_link/ingest/github_release.py` - harden latest asset filtering and `pg_restore` error reporting.
- `.github/workflows/build-corpus.yml` - disable/remove CPU builder after verifier exists.
- `Makefile` - add `bundle-validate`, `bundle-publish-local`, and `cuda-check`.
- `.env.docker.example` - document Docker release restore as the production path.
- `docker/README.md` - document `BUNDLE_URL=latest` and pinned release URLs.
- `README.md` - document maintainer local RTX build and Docker restore path.
- `tests/test_docker_compose_config.py` - assert `BUNDLE_URL` remains passed to the service.
- `tests/integration/test_bundle_round_trip.py` - assert extended manifest fields for mini round trip.

---

## Phase 1 - Bundle Metadata and Naming

### Task 1: Add release-id and asset-name helpers

**Files:**

- Create: `genereview_link/corpus/bundle_metadata.py`
- Test: `tests/unit/test_corpus_bundle_metadata.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_corpus_bundle_metadata.py`:

```python
"""Tests for corpus bundle release metadata helpers."""

from __future__ import annotations

import pytest

from genereview_link.corpus.bundle_metadata import (
    asset_name_for_release,
    validate_release_id,
)


@pytest.mark.parametrize("release_id", ["2026-05-12-r1", "2026-12-31-r12"])
def test_validate_release_id_accepts_corpus_release_ids(release_id: str) -> None:
    assert validate_release_id(release_id) == release_id


@pytest.mark.parametrize("release_id", ["corpus-2026-05-12-r1", "20260512-r1", "2026-05-12", "latest"])
def test_validate_release_id_rejects_invalid_values(release_id: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD-rN"):
        validate_release_id(release_id)


def test_asset_name_for_release_includes_model_and_database_versions() -> None:
    assert asset_name_for_release(
        "2026-05-12-r1",
        model_slug="bge-small-en-v1.5",
        postgres_major="pg18",
        pgvector_version="pgv0.8.2",
    ) == "genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle_metadata.py -v
```

Expected: import error for `genereview_link.corpus.bundle_metadata`.

- [ ] **Step 3: Implement helper module**

Create `genereview_link/corpus/bundle_metadata.py`:

```python
"""Metadata helpers for corpus bundle releases."""

from __future__ import annotations

import re

RELEASE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-r[1-9]\d*$")


def validate_release_id(release_id: str) -> str:
    """Validate the release id component used in corpus release tags."""
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError("release_id must use YYYY-MM-DD-rN, for example 2026-05-12-r1")
    return release_id


def asset_name_for_release(
    release_id: str,
    *,
    model_slug: str,
    postgres_major: str,
    pgvector_version: str,
) -> str:
    """Return the canonical tarball asset name for a corpus release."""
    validated = validate_release_id(release_id)
    return (
        f"genereview-corpus-{validated}-{model_slug}-"
        f"{postgres_major}-{pgvector_version}.tar.gz"
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle_metadata.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/bundle_metadata.py tests/unit/test_corpus_bundle_metadata.py
git commit -m "feat: add corpus bundle release naming helpers"
```

### Task 2: Extend bundle manifest fields

**Files:**

- Modify: `genereview_link/corpus/bundle.py`
- Test: `tests/unit/test_corpus_bundle.py`

- [ ] **Step 1: Write failing manifest serialization test**

Append this test to `tests/unit/test_corpus_bundle.py`:

```python
def test_bundle_manifest_includes_release_provenance_fields() -> None:
    manifest = BundleManifest(
        corpus_release_id="2026-05-12-r1",
        app_git_sha="abc123",
        schema_migrations={"control": ["0001_base"], "data": ["genereview:0001_chapters"]},
        validation={"status": "passed", "smoke_queries": []},
    )

    payload = asdict(manifest)

    assert payload["corpus_release_id"] == "2026-05-12-r1"
    assert payload["app_git_sha"] == "abc123"
    assert payload["schema_migrations"]["control"] == ["0001_base"]
    assert payload["validation"]["status"] == "passed"
```

If `asdict` is not already imported in that test file, add:

```python
from dataclasses import asdict
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle.py::test_bundle_manifest_includes_release_provenance_fields -v
```

Expected: `TypeError` because `BundleManifest` does not accept the new fields.

- [ ] **Step 3: Extend `BundleManifest`**

Modify `genereview_link/corpus/bundle.py` so `BundleManifest` contains these fields:

```python
    corpus_release_id: str = ""
    app_git_sha: str = ""
    app_version: str = ""
    schema_migrations: dict[str, list[str]] = field(
        default_factory=lambda: {"control": [], "data": []}
    )
    hnsw: dict[str, object] = field(
        default_factory=lambda: {
            "index_name": "genereview_embeddings_bge384_hnsw_cosine",
            "exists": False,
        }
    )
    source: dict[str, object] = field(default_factory=dict)
    validation: dict[str, object] = field(
        default_factory=lambda: {"status": "not_run", "smoke_queries": []}
    )
```

Remove or replace the existing `schema_migrations: list[str]` field so the manifest has one canonical shape.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle.py tests/unit/test_corpus_bundle_metadata.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/bundle.py tests/unit/test_corpus_bundle.py
git commit -m "feat: extend corpus bundle manifest provenance"
```

---

## Phase 2 - Validation

### Task 3: Add reusable bundle readiness validation

**Files:**

- Create: `genereview_link/corpus/bundle_validation.py`
- Test: `tests/unit/test_corpus_bundle_validation.py`

- [ ] **Step 1: Write unit tests for validation result behavior**

Create `tests/unit/test_corpus_bundle_validation.py`:

```python
"""Tests for bundle validation result helpers."""

from __future__ import annotations

from genereview_link.corpus.bundle_validation import BundleValidationResult


def test_validation_result_passes_when_no_errors() -> None:
    result = BundleValidationResult(errors=[], warnings=["passage count close to threshold"])

    assert result.ok is True
    assert result.as_manifest()["status"] == "passed"
    assert result.as_manifest()["warnings"] == ["passage count close to threshold"]


def test_validation_result_fails_when_errors_exist() -> None:
    result = BundleValidationResult(errors=["embeddings incomplete"], warnings=[])

    assert result.ok is False
    assert result.as_manifest()["status"] == "failed"
    assert result.as_manifest()["errors"] == ["embeddings incomplete"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle_validation.py -v
```

Expected: import error for `bundle_validation`.

- [ ] **Step 3: Implement validation result and SQL validator**

Create `genereview_link/corpus/bundle_validation.py`:

```python
"""Validation helpers for publishable corpus bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(frozen=True)
class BundleValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_manifest(self) -> dict[str, Any]:
        return {
            "status": "passed" if self.ok else "failed",
            "errors": self.errors,
            "warnings": self.warnings,
            "smoke_queries": [],
        }


async def validate_database_ready(
    pool: asyncpg.Pool,
    *,
    schema: str = "genereview",
    min_chapters: int = 880,
    min_passages: int = 40_000,
    embedding_table: str = "genereview_embeddings_bge384",
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> BundleValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    async with pool.acquire() as conn:
        active_version = await conn.fetchval(
            "select version from public.genereview_corpus_version where is_active"
        )
        if not active_version:
            errors.append("no active corpus version")

        chapter_count = int(
            await conn.fetchval(f'select count(*) from "{schema}".genereview_chapters') or 0
        )
        passage_count = int(
            await conn.fetchval(f'select count(*) from "{schema}".genereview_passages') or 0
        )
        embedding_count = int(
            await conn.fetchval(
                f'select count(*) from "{schema}".{embedding_table} where model_name = $1',
                model_name,
            )
            or 0
        )
        hnsw_exists = bool(
            await conn.fetchval(
                """
                select exists (
                  select 1 from pg_indexes
                   where schemaname = $1
                     and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
                )
                """,
                schema,
            )
        )
        active_embedding = await conn.fetchrow(
            """
            select table_name, model_name
              from public.genereview_active_embedding
             where id = 1
            """
        )

    if chapter_count < min_chapters:
        errors.append(f"chapter count {chapter_count} is below minimum {min_chapters}")
    if passage_count < min_passages:
        errors.append(f"passage count {passage_count} is below minimum {min_passages}")
    if embedding_count != passage_count:
        errors.append(f"embedding count {embedding_count} does not equal passage count {passage_count}")
    if not hnsw_exists:
        errors.append("HNSW index genereview_embeddings_bge384_hnsw_cosine is missing")
    if active_embedding is None:
        errors.append("public.genereview_active_embedding row is missing")
    elif active_embedding["table_name"] != embedding_table or active_embedding["model_name"] != model_name:
        errors.append("public.genereview_active_embedding does not match bundled embedding table/model")

    return BundleValidationResult(errors=errors, warnings=warnings)
```

- [ ] **Step 4: Run validation tests**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle_validation.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/bundle_validation.py tests/unit/test_corpus_bundle_validation.py
git commit -m "feat: add corpus bundle validation helpers"
```

### Task 4: Add `bundle validate` CLI

**Files:**

- Modify: `genereview_link/cli.py`
- Modify: `Makefile`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add CLI test**

Append to `tests/test_cli.py`:

```python
def test_bundle_validate_command_registered() -> None:
    result = runner.invoke(app, ["bundle", "validate", "--help"])

    assert result.exit_code == 0
    assert "Validate that DATABASE_URL is ready for bundle publishing" in result.output
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_validate_command_registered -v
```

Expected: Typer reports no such command.

- [ ] **Step 3: Implement command**

In `genereview_link/cli.py`, add under `bundle_app`:

```python
@bundle_app.command("validate")
def bundle_validate() -> None:
    """Validate that DATABASE_URL is ready for bundle publishing."""
    import asyncio

    from genereview_link.corpus.bundle_validation import validate_database_ready
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            result = await validate_database_ready(pool)
            for warning in result.warnings:
                typer.echo(f"warning: {warning}")
            if not result.ok:
                for error in result.errors:
                    typer.echo(f"error: {error}", err=True)
                raise typer.Exit(1)
            typer.echo("bundle validation passed")
        finally:
            await pool.close()

    asyncio.run(run())
```

- [ ] **Step 4: Add Makefile target**

Modify `.PHONY` to include `bundle-validate`, then add:

```make
bundle-validate: ## Validate active corpus is ready for bundle publishing
	uv run genereview-link bundle validate
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_validate_command_registered -v
```

Expected: test passes.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/cli.py Makefile tests/test_cli.py
git commit -m "feat: add corpus bundle validation command"
```

---

## Phase 3 - Local RTX Publish Command

### Task 5: Add CUDA preflight script and Makefile target

**Files:**

- Create: `scripts/verify_torch_cuda.py`
- Modify: `Makefile`

- [ ] **Step 1: Create CUDA preflight script**

Create `scripts/verify_torch_cuda.py`:

```python
"""Verify that PyTorch can see a CUDA device."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import torch
    except ImportError:
        print("torch is not installed", file=sys.stderr)
        return 1
    print(f"torch={torch.__version__}")
    available = torch.cuda.is_available()
    print(f"cuda_available={available}")
    if not available:
        return 1
    print(f"cuda_device={torch.cuda.get_device_name(0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Add Makefile target**

Modify `.PHONY` to include `cuda-check`, then add:

```make
cuda-check: ## Verify local PyTorch CUDA availability
	uv run python scripts/verify_torch_cuda.py
```

- [ ] **Step 3: Run CUDA preflight locally**

Run:

```bash
make cuda-check
```

Expected on maintainer workstation:

```text
torch=2.11.0+cu130
cuda_available=True
cuda_device=NVIDIA GeForce RTX 5090
```

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_torch_cuda.py Makefile
git commit -m "chore: add local CUDA preflight"
```

### Task 6: Improve `bundle build` with release-id naming and validation

**Files:**

- Modify: `genereview_link/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add CLI help test for new options**

Append to `tests/test_cli.py`:

```python
def test_bundle_build_exposes_release_id_option() -> None:
    result = runner.invoke(app, ["bundle", "build", "--help"])

    assert result.exit_code == 0
    assert "--release-id" in result.output
    assert "--skip-validation" in result.output
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_build_exposes_release_id_option -v
```

Expected: assertion failure because options are absent.

- [ ] **Step 3: Extract the build helper and update `bundle_build`**

Rename the current `bundle_build` function to `_build_bundle`, remove the Typer `Annotated` option defaults from its parameters, and make it return the built `Path`. Its signature must be:

```python
def _build_bundle(
    *,
    output: Path | None,
    release_id: str | None,
    skip_validation: bool,
) -> Path:
    """Build a corpus bundle from DATABASE_URL and return the tarball path."""
```

Keep the current async `run()` structure inside `_build_bundle`, but change the final line after the `write_bundle` call from CLI echoing to `return output`. Then add a new Typer command wrapper:

```python
@bundle_app.command("build")
def bundle_build(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    release_id: Annotated[str | None, typer.Option("--release-id")] = None,
    skip_validation: Annotated[
        bool,
        typer.Option("--skip-validation", help="Build without publish-readiness validation."),
    ] = False,
) -> None:
    """Build a release bundle from the current DATABASE_URL."""
    built = _build_bundle(output=output, release_id=release_id, skip_validation=skip_validation)
    typer.echo(f"wrote {built} (+ {built}.sha256)")
```

- [ ] **Step 4: Use canonical asset name when `--release-id` is supplied inside `_build_bundle`**

Inside `_build_bundle`, before `write_bundle`, compute output like this:

```python
from genereview_link.corpus.bundle_metadata import asset_name_for_release
from genereview_link.corpus.bundle_validation import validate_database_ready

if release_id and output is None:
    output = Path(
        asset_name_for_release(
            release_id,
            model_slug="bge-small-en-v1.5",
            postgres_major="pg18",
            pgvector_version="pgv0.8.2",
        )
    )
output = output or Path("genereview-corpus.tar.gz")
```

Before dumping, run:

```python
validation_manifest = {"status": "not_run", "smoke_queries": []}
if not skip_validation:
    validation = await validate_database_ready(pool)
    validation_manifest = validation.as_manifest()
    if not validation.ok:
        for error in validation.errors:
            typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
```

When constructing `BundleManifest`, pass:

```python
corpus_release_id=release_id or "",
validation=validation_manifest,
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_build_exposes_release_id_option tests/unit/test_corpus_bundle_metadata.py -v
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/cli.py tests/test_cli.py
git commit -m "feat: add release-aware corpus bundle build"
```

### Task 7: Add `bundle publish-local`

**Files:**

- Modify: `genereview_link/cli.py`
- Modify: `Makefile`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add command registration test**

Append to `tests/test_cli.py`:

```python
def test_bundle_publish_local_command_registered() -> None:
    result = runner.invoke(app, ["bundle", "publish-local", "--help"])

    assert result.exit_code == 0
    assert "--release-id" in result.output
    assert "--device" in result.output
    assert "--repo" in result.output
    assert "--draft" in result.output
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_publish_local_command_registered -v
```

Expected: Typer reports no such command.

- [ ] **Step 3: Implement GitHub upload helper**

In `genereview_link/cli.py`, add near bundle commands:

```python
def _run_gh(args: list[str]) -> None:
    import subprocess

    try:
        subprocess.run(["gh", *args], check=True)  # noqa: S603, S607
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI 'gh' is required for upload") from exc
```

- [ ] **Step 4: Implement command**

Add under `bundle_app`:

```python
@bundle_app.command("publish-local")
def bundle_publish_local(
    release_id: Annotated[str, typer.Option("--release-id")],
    device: Annotated[str, typer.Option("--device")] = "cuda",
    repo: Annotated[str, typer.Option("--repo")] = "berntpopp/genereviews-link",
    draft: Annotated[bool, typer.Option("--draft/--no-draft")] = True,
    upload: Annotated[bool, typer.Option("--upload/--no-upload")] = True,
) -> None:
    """Build a local CUDA corpus bundle and optionally upload it to GitHub Releases."""
    import asyncio
    import os
    from pathlib import Path

    from genereview_link.corpus.bundle_metadata import asset_name_for_release, validate_release_id
    from genereview_link.corpus.pipeline import run_full_ingest
    from genereview_link.db.migrate import apply_control_migrations
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
    from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

    validate_release_id(release_id)
    if device.startswith("cuda"):
        import torch

        if not torch.cuda.is_available():
            typer.echo("CUDA requested but torch.cuda.is_available() is false", err=True)
            raise typer.Exit(1)

    output = Path(
        asset_name_for_release(
            release_id,
            model_slug="bge-small-en-v1.5",
            postgres_major="pg18",
            pgvector_version="pgv0.8.2",
        )
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            await apply_control_migrations(pool)
            await run_full_ingest(pool)
            os.environ["INGEST_EMBED_DEVICE"] = device
            provider = SentenceTransformerEmbeddingProvider(device=device)
            embedded = await backfill_embeddings(pool, provider)
            typer.echo(f"embedded {embedded} passages")
            await build_hnsw_index(pool)
        finally:
            await pool.close()

    asyncio.run(run())
    built = _build_bundle(output=output, release_id=release_id, skip_validation=False)

    if upload:
        tag = f"corpus-{release_id}"
        release_args = ["release", "create", tag, str(built), f"{built}.sha256", "--repo", repo]
        if draft:
            release_args.append("--draft")
        release_args.extend(["--title", tag, "--notes", f"Precomputed GeneReviews corpus bundle {release_id}"])
        _run_gh(release_args)
        typer.echo(f"uploaded {built} to {repo} release {tag}")
    else:
        typer.echo(f"built {built}; upload skipped")
```

- [ ] **Step 5: Add Makefile target**

Modify `.PHONY` to include `bundle-publish-local`, then add:

```make
bundle-publish-local: ## Build/publish corpus bundle locally; set RELEASE_ID=YYYY-MM-DD-rN
	uv run genereview-link bundle publish-local --release-id $${RELEASE_ID:?set RELEASE_ID=YYYY-MM-DD-rN}
```

- [ ] **Step 6: Run CLI registration test**

Run:

```bash
uv run pytest tests/test_cli.py::test_bundle_publish_local_command_registered -v
```

Expected: test passes.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/cli.py Makefile tests/test_cli.py
git commit -m "feat: add local corpus bundle publisher"
```

---

## Phase 4 - Restore and Release Verification

### Task 8: Harden GitHub Release restore helpers

**Files:**

- Modify: `genereview_link/ingest/github_release.py`
- Test: add focused tests to `tests/unit/test_corpus_bundle.py` or create `tests/unit/test_github_release.py`

- [ ] **Step 1: Add tests for asset filtering**

Create `tests/unit/test_github_release.py`:

```python
"""Tests for GitHub release bundle helpers."""

from __future__ import annotations

from genereview_link.ingest.github_release import _select_bundle_asset


def test_select_bundle_asset_picks_genereview_corpus_tarball() -> None:
    assets = [
        {"name": "notes.txt", "browser_download_url": "https://example/notes.txt"},
        {"name": "genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz", "browser_download_url": "https://example/bundle.tar.gz"},
    ]

    assert _select_bundle_asset(assets) == "https://example/bundle.tar.gz"


def test_select_bundle_asset_ignores_sha256_and_other_tarballs() -> None:
    assets = [
        {"name": "genereview-corpus-2026-05-12-r1.tar.gz.sha256", "browser_download_url": "https://example/sha"},
        {"name": "other.tar.gz", "browser_download_url": "https://example/other.tar.gz"},
    ]

    assert _select_bundle_asset(assets) is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/test_github_release.py -v
```

Expected: import error for `_select_bundle_asset`.

- [ ] **Step 3: Add helper and use it in `resolve_latest`**

In `genereview_link/ingest/github_release.py`, add:

```python
def _select_bundle_asset(assets: list[dict[str, object]]) -> str | None:
    for asset in assets:
        name = str(asset.get("name", ""))
        if (
            name.startswith("genereview-corpus-")
            and name.endswith(".tar.gz")
            and not name.endswith(".sha256")
        ):
            return str(asset["browser_download_url"])
    return None
```

Then replace the loop in `resolve_latest` with:

```python
        selected = _select_bundle_asset(r.json().get("assets", []))
        if selected:
            return selected
```

- [ ] **Step 4: Improve `pg_restore` stderr reporting**

Change `pg_restore` to capture output:

```python
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore failed with exit {result.returncode}: {result.stderr.strip()}")
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_github_release.py -v
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/ingest/github_release.py tests/unit/test_github_release.py
git commit -m "fix: harden corpus release asset restore helpers"
```

### Task 9: Replace CPU corpus builder workflow with verifier

**Files:**

- Create: `.github/workflows/verify-corpus-bundle.yml`
- Modify: `.github/workflows/build-corpus.yml`

- [ ] **Step 1: Add verifier workflow**

Create `.github/workflows/verify-corpus-bundle.yml`:

```yaml
name: Verify corpus bundle

on:
  workflow_dispatch:
    inputs:
      bundle_url:
        description: "GitHub Release asset URL ending in .tar.gz"
        required: true
        type: string

concurrency:
  group: verify-corpus-bundle
  cancel-in-progress: false

jobs:
  verify:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:0.8.2-pg18
        env:
          POSTGRES_PASSWORD: ci
          POSTGRES_DB: genereview
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
    env:
      DATABASE_URL: postgresql://postgres:ci@localhost:5432/genereview
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - name: Download bundle and checksum
        run: |
          mkdir -p dist verify
          curl -fL "${{ inputs.bundle_url }}" -o dist/bundle.tar.gz
          curl -fL "${{ inputs.bundle_url }}.sha256" -o dist/bundle.tar.gz.sha256
          cd dist
          sha256sum -c bundle.tar.gz.sha256
      - name: Extract and verify manifest checksums
        run: |
          tar -xzf dist/bundle.tar.gz -C verify
          uv run python - <<'PY'
          import hashlib, json
          from pathlib import Path

          root = Path("verify")
          manifest = json.loads((root / "manifest.json").read_text())
          for relpath, expected in manifest["checksums"].items():
              h = hashlib.sha256((root / relpath).read_bytes()).hexdigest()
              if h != expected:
                  raise SystemExit(f"checksum mismatch for {relpath}: {h} != {expected}")
          print("manifest checksums passed")
          PY
      - name: Restore dump
        run: pg_restore -j 2 -d "$DATABASE_URL" verify/corpus.dump
      - name: Validate restored corpus
        run: |
          psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
          select version, chapter_count, ingest_status, is_active
            from public.genereview_corpus_version
           where is_active;
          select count(*) as chapters from genereview.genereview_chapters;
          select count(*) as passages from genereview.genereview_passages;
          select count(*) as embeddings from genereview.genereview_embeddings_bge384;
          select indexname from pg_indexes
           where schemaname = 'genereview'
             and indexname = 'genereview_embeddings_bge384_hnsw_cosine';
          SQL
      - name: Summary
        run: |
          echo "## Corpus Bundle Verification" >> "$GITHUB_STEP_SUMMARY"
          echo "" >> "$GITHUB_STEP_SUMMARY"
          echo "Verified: ${{ inputs.bundle_url }}" >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Disable old scheduled CPU builder**

Replace `.github/workflows/build-corpus.yml` contents with:

```yaml
name: Build corpus bundle

on:
  workflow_dispatch:
    inputs:
      disabled_notice:
        description: "This workflow is disabled. Build locally with bundle publish-local, then run Verify corpus bundle."
        required: false
        default: "disabled"
        type: string

jobs:
  disabled:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "Corpus bundles are built locally on the maintainer RTX workstation."
          echo "Use: uv run genereview-link bundle publish-local --release-id YYYY-MM-DD-rN"
          echo "Then run .github/workflows/verify-corpus-bundle.yml against the release asset URL."
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/verify-corpus-bundle.yml .github/workflows/build-corpus.yml
git commit -m "ci: verify corpus release bundles instead of building on CPU"
```

---

## Phase 5 - Docker and Docs

### Task 10: Document Docker release restore defaults

**Files:**

- Modify: `.env.docker.example`
- Modify: `docker/README.md`
- Modify: `README.md`

- [ ] **Step 1: Update `.env.docker.example` corpus section**

Replace the corpus bootstrap comments with:

```env
# Corpus bootstrap
# Production path: set BUNDLE_URL to "latest" or to a pinned GitHub Release
# asset URL. Docker will download, verify, and pg_restore the bundle on first
# boot when the Postgres volume has no active corpus.
#
# Convenience after the first promoted corpus release:
# BUNDLE_URL=latest
#
# Reproducible deployments should pin a release asset URL:
# BUNDLE_URL=https://github.com/berntpopp/genereviews-link/releases/download/corpus-2026-05-12-r1/genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
BUNDLE_URL=latest
BUILD_LOCAL=false
AUTO_PULL_RELEASES=false
```

- [ ] **Step 2: Update `docker/README.md`**

Add a short production note:

````markdown
### Corpus Bundle Restore

Production Docker should restore a precomputed GitHub Release bundle:

```bash
BUNDLE_URL=latest
```

For reproducibility, pin the release asset URL instead of using `latest`.
Docker does not run ingest/backfill unless `BUILD_LOCAL=true` is explicitly set.
````

- [ ] **Step 3: Update `README.md` deployment modes**

In the Mode 1 section, clarify:

```markdown
This is the recommended Docker production mode. The bundle is built locally by
the maintainer on CUDA, published as a GitHub Release asset, and consumed by
Docker at startup.
```

Add a maintainer command example:

```bash
make cuda-check
RELEASE_ID=2026-05-12-r1 make bundle-publish-local
```

- [ ] **Step 4: Commit**

```bash
git add .env.docker.example docker/README.md README.md
git commit -m "docs: make release bundle restore the Docker default"
```

### Task 11: Keep Docker compose config covered

**Files:**

- Modify: `tests/test_docker_compose_config.py`

- [ ] **Step 1: Add assertion for bundle documentation**

Add or update a test in `tests/test_docker_compose_config.py`:

```python
def test_env_docker_example_documents_bundle_url() -> None:
    env_example = Path(".env.docker.example").read_text()

    assert "BUNDLE_URL=latest" in env_example
    assert "BUILD_LOCAL=false" in env_example
```

- [ ] **Step 2: Run Docker config tests**

Run:

```bash
uv run pytest tests/test_docker_compose_config.py -v
```

Expected: tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_docker_compose_config.py
git commit -m "test: cover Docker corpus bundle configuration"
```

---

## Phase 6 - End-to-End Verification

### Task 12: Run local verification suite

**Files:** no source changes expected.

- [ ] **Step 1: Run fast focused checks**

Run:

```bash
uv run pytest tests/unit/test_corpus_bundle_metadata.py tests/unit/test_corpus_bundle_validation.py tests/unit/test_github_release.py tests/test_cli.py tests/test_docker_compose_config.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run formatting and linting**

Run:

```bash
make format
make lint
```

Expected: Ruff formatting/linting passes.

- [ ] **Step 3: Run type checking**

Run:

```bash
make typecheck
```

Expected: mypy passes.

- [ ] **Step 4: Run full local CI**

Run:

```bash
make ci-local
```

Expected: all required local checks pass.

### Task 13: Perform first local RTX release build

**Files:** generated release artifacts only; source changes should already be committed.

- [ ] **Step 1: Confirm CUDA**

Run:

```bash
make cuda-check
```

Expected:

```text
cuda_available=True
cuda_device=NVIDIA GeForce RTX 5090
```

- [ ] **Step 2: Build and upload draft release**

Run:

```bash
RELEASE_ID=2026-05-12-r1 make bundle-publish-local
```

Expected:

```text
embedded 40853 passages
wrote genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
uploaded genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz to berntpopp/genereviews-link release corpus-2026-05-12-r1
```

- [ ] **Step 3: Run GitHub verifier workflow**

Open GitHub Actions, run `Verify corpus bundle`, and pass the draft asset URL:

```text
https://github.com/berntpopp/genereviews-link/releases/download/corpus-2026-05-12-r1/genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz
```

Expected: workflow passes manifest checksum verification, `pg_restore`, and SQL validation.

- [ ] **Step 4: Promote release**

Run:

```bash
gh release edit corpus-2026-05-12-r1 --repo berntpopp/genereviews-link --draft=false --prerelease=false
```

Expected: release is public and non-prerelease.

- [ ] **Step 5: Test Docker restore on fresh volume**

Run:

```bash
docker compose -f docker/docker-compose.yml down -v
BUNDLE_URL=latest docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f genereview-link
```

Expected logs contain:

```text
downloading corpus bundle
corpus bundle restored
```

Then query:

```bash
curl -fsS 'http://localhost:8000/passages/search?q=BRCA1%20risk-reducing%20mastectomy&limit=1'
```

Expected: HTTP 200 with at least one passage result and `_meta.corpus_version`.

### Task 14: Final commit and tag hygiene

**Files:** no source changes expected unless verification revealed fixes.

- [ ] **Step 1: Inspect status**

Run:

```bash
git status --short
```

Expected: only intended generated artifacts are untracked, or clean if artifacts are outside the repo.

- [ ] **Step 2: Do not commit generated tarballs**

If tarballs were created in the repo root, remove or move them outside the repo:

```bash
rm -f genereview-corpus-*.tar.gz genereview-corpus-*.tar.gz.sha256
```

Expected: no release artifact remains tracked or staged.

- [ ] **Step 3: Final required check**

Run:

```bash
make ci-local
```

Expected: passes.

- [ ] **Step 4: Record release URL in issue 27**

Write the GitHub issue comment body:

```bash
cat > /tmp/issue-27-bundle-comment.md <<'EOF'
Published first precomputed corpus bundle:

- Release: https://github.com/berntpopp/genereviews-link/releases/tag/corpus-2026-05-12-r1
- Docker convenience setting: `BUNDLE_URL=latest`
- Pinned setting: `BUNDLE_URL=https://github.com/berntpopp/genereviews-link/releases/download/corpus-2026-05-12-r1/genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz`
- Verification: GitHub Actions `Verify corpus bundle` passed.
EOF
```

Post the comment:

```bash
gh issue comment 27 --repo berntpopp/genereviews-link --body-file /tmp/issue-27-bundle-comment.md
```

Expected: comment is posted to issue 27.
