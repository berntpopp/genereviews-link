# GeneReviews Final Publication Closure Implementation Plan

> Historical record — implementation plan executed on 2026-09-01.

Status: completed as one atomic change; verification evidence is recorded in the handoff report.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the accepted production-image, immutable-publication, provenance, and controller-readiness findings without publishing or mutating external state.

**Architecture:** Keep the data plane split into five independently testable boundaries. The production image carries a complete installed application and exact PostgreSQL 18 clients; durable handoff locators and bundle-only rights records identify transferable bytes; a sealed stdlib-only release transaction validates resumable drafts and creates an exact annotated tag atomically; offline source admission verifies the prior manifest and persists computation identity before staging mutation; restore writes one final controller-compatible readiness record only after all semantic checks pass.

**Tech Stack:** Python 3.12, PostgreSQL 18 with pgvector 0.8.2, asyncpg, Docker/OCI, GitHub Actions, Typer, pytest.

**Spec:** `/home/bernt-popp/development/genefoundry-router/.worktrees/fleet-closure-20260830/docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md` and Task 8 of `/home/bernt-popp/development/genefoundry-router/.worktrees/fleet-closure-20260830/docs/superpowers/plans/2026-08-30-data-pipeline-and-release-remediation.md`

## Global Constraints

- Rights absent or ambiguous fails before any GitHub release or tag mutation.
- No external publication, upload, SSH, deployment, production mutation, or data publication is performed during implementation.
- Every behavior change follows a witnessed RED/GREEN cycle and the result is one atomic commit.
- Release assets remain exactly `corpus.dump`, `manifest.json`, `SHA256SUMS`, `rights-record.json`, `rights-evidence.json`, `terms-snapshot.html`, `seal-manifest.json`, and `publisher-tool.whl`.
- The controller readiness JSON uses the exact reviewed operation order and the logical volumes are exactly `genereview_pg_data`, `genereview_pg_run`, and `genereview_restore_state`.

---

### Task 1: Complete and version the production image

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `container-release.json`
- Test: `tests/unit/test_final_publication_closure.py`

**Interfaces:**
- Consumes: the digest-pinned `pgvector/pgvector:0.8.2-pg18` build stage and the installed wheel.
- Produces: a production image in which `genereview_link`, `genereview_link.corpus`, `genereview-link`, `pg_dump`, `pg_restore`, and `psql` execute, with every PostgreSQL client reporting major 18.

- [ ] Add a regression that builds the production target and runs import, CLI, migration-resource, and client-version probes inside the resulting OCI image.
- [ ] Run that regression and record failure from the removed package and PostgreSQL 17 client.
- [ ] Copy the exact PG18 client binaries from the existing digest-pinned PG18 stage, stop deleting installed application modules, and add `0007_embedding_run_identity.sql` to the image allowlist.
- [ ] Re-run the focused regression and inspect the built image's configured user, package paths, and client versions.

### Task 2: Make handoff and rights evidence transferable

**Files:**
- Create: `genereview_link/corpus/handoff_locator.py`
- Modify: `genereview_link/corpus/rights.py`
- Modify: `.github/workflows/corpus-data-release.yml`
- Modify: `docs/data.md`
- Test: `tests/unit/test_final_publication_closure.py`

**Interfaces:**
- Consumes: a protected locator no larger than 48 KiB whose exact immutable GitHub release-asset URLs, sizes, and SHA-256 values name one sealed handoff object retained outside Actions.
- Produces: a fresh owner-only local reconstruction; rights `terms_uri` and `evidence_uri` resolve only to `bundle:terms-snapshot.html` and `bundle:rights-evidence.json` in the fetched rights directory.

- [ ] Add regressions rejecting Actions run-artifact identity, absolute paths, `file:` paths, traversal, incomplete locator assets, and digest mismatches.
- [ ] Run the regressions and record current acceptance of local paths and workflow-run handoff transfer.
- [ ] Implement the bounded allowlisted handoff locator/fetcher and restrict rights verification to exact bundle members.
- [ ] Change workflow inputs to an exact protected durable handoff locator and remove `RUNNER_TEMP`/Actions-artifact retention claims.
- [ ] Re-run locator, rights, handoff, and workflow tests.

### Task 3: Make draft publication resumable and tag creation race-free

**Files:**
- Create: `genereview_link/corpus/release_transaction.py`
- Modify: `.github/workflows/corpus-data-release.yml`
- Test: `tests/unit/test_final_publication_closure.py`

**Interfaces:**
- Consumes: frozen expected release identity plus the current draft/published representation.
- Produces: one of `create`, `resume`, `promote`, or `immutable-noop`, and an exact ordered set of missing assets. Existing draft assets must be an exact unique subset with matching size/digest; otherwise publication fails before upload.

- [ ] Add table-driven state tests for absent release, exact partial draft, mismatching partial draft, exact complete draft, immutable exact no-op, and wrong-target/tag states.
- [ ] Run the tests and record the missing transaction interface.
- [ ] Implement strict state classification and missing-asset planning in the sealed wheel.
- [ ] Update publication to resume only missing assets, create an annotated tag object and tag ref atomically at the final serialized point, verify its peeled target, and only then conditionally publish the frozen draft.
- [ ] Execute the state-machine CLI against fixtures and re-run workflow/promotion tests.

### Task 4: Bind prior artifact and pre-mutation ingest provenance

**Files:**
- Modify: `genereview_link/corpus/source_locator.py`
- Modify: `genereview_link/corpus/source_capture.py`
- Modify: `genereview_link/corpus/pipeline.py`
- Modify: `genereview_link/cli.py`
- Modify: `.github/workflows/corpus-data-release.yml`
- Modify: `.github/workflows/verify-corpus-bundle.yml`
- Test: `tests/unit/test_final_publication_closure.py`
- Test: `tests/integration/test_ingest_end_to_end.py`

**Interfaces:**
- Consumes: an explicit retained `prior-manifest.json` whose locator identity is immutable and whose logical content identity must equal `source_capture.prior_artifact`.
- Produces: one atomically inserted corpus/source admission and immutable ingest run before `genereview_staging` is dropped or recreated.

- [ ] Add regressions showing fabricated prior tuples pass and staging mutation precedes source/run admission.
- [ ] Run both regressions and record their failure behavior.
- [ ] Require and verify the prior manifest, then atomically insert corpus and ingest-run records under the writer lock before staging mutation.
- [ ] Re-run source, pipeline, computation-run, and real PostgreSQL ingest tests.

### Task 5: Write the last controller readiness record

**Files:**
- Create: `genereview_link/db/migrations/control/0007_release_readiness.sql`
- Create: `genereview_link/corpus/readiness.py`
- Modify: `genereview_link/corpus/schema_identity.py`
- Modify: `genereview_link/cli.py`
- Modify: `container-release.json`
- Modify: `.github/workflows/verify-corpus-bundle.yml`
- Test: `tests/unit/test_final_publication_closure.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: the restored manifest plus actual active corpus rows, migrations, source digest, HNSW catalog entry, and representative semantic evaluation from the restored database.
- Produces: exactly one active `public.genereview_release_readiness` row whose `readiness` JSON has the controller's exact fields, `restore_count=1`, `restore_mode=data-only`, nonzero semantic digest, and ordered terminal `readiness-marker` step.

- [ ] Add unit and PG18 integration regressions proving no readiness row exists before semantic completion and malformed counts/migrations/index/source/query identities cannot be written.
- [ ] Run the regressions and record the absent table/writer failures.
- [ ] Add the reviewed control migration and a single transactional writer called only after restore, HNSW construction, content/computation checks, and fresh evaluation match the manifest.
- [ ] Re-run restore, migration, bundle round-trip, and controller-shape tests against PostgreSQL 18/pgvector.

### Task 6: Verify and commit the closure

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/data.md`
- Verify: all files changed by Tasks 1-5

**Interfaces:**
- Consumes: all focused green tests and the built production image.
- Produces: one clean atomic commit containing implementation, regressions, and documentation.

- [ ] Run Ruff formatting/checks, mypy, LOC, focused unit/integration tests, the real production OCI smoke, and `make ci-local`.
- [ ] Review the complete diff against all ten accepted requirements and run `git diff --check`.
- [ ] Create one atomic commit and verify exact clean HEAD without pushing or invoking external mutation.
