---
name: release-readiness
description: Use before tagging a release of genereviews-link.
---

# Release Readiness

Follow `AGENTS.md` first.

## Workflow

1. Verify `make ci-local` is green on `main`.
2. Run `make test-cov` and confirm coverage is above the 70% floor.
3. Run `make docker-build` and verify it succeeds.
4. Update `pyproject.toml` `version`. Use semantic versioning.
5. Update `README.md` if behavior changed.
6. Create a tag `vX.Y.Z` and push. The `release.yml` workflow handles
   wheel build and TestPyPI publication.
7. Verify the release artifact downloads and installs cleanly:
   `uv run pip install --index-url https://test.pypi.org/simple/ genereview-link==X.Y.Z`.
