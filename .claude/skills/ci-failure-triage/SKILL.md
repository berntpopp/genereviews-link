---
name: ci-failure-triage
description: Use when `make ci-local` fails or a GitHub Actions run reports a CI failure.
---

# CI Failure Triage

Follow `AGENTS.md` first.

## Workflow

1. Run `make ci-local` locally and identify which sub-target failed
   (format-check, lint-ci, typecheck-fast, or test-fast).
2. For format failures: run `make format` and re-check.
3. For lint failures: run `make lint-fix` and address remaining manual issues.
4. For typecheck failures: read the mypy error, fix the type annotation —
   do not silence with blanket `# type: ignore`. Use `# type: ignore[specific-code]`
   only for legitimate library typing gaps.
5. For test failures: read the assertion, fix the code or test (not the
   assertion to mask the bug). For scraper tests, consider refreshing
   `tests/fixtures/` only if scraper logic intentionally changed.
6. Re-run `make ci-local` until green.
