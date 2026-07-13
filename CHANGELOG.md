# Changelog

All notable changes to GeneReviews-Link are documented in this file.

## [Unreleased]

## [5.0.4] - 2026-07-13

### Added

- Adopt the GeneFoundry router container-release standard with SHA-pinned
  reusable container CI/release callers, digest-only production image
  configuration, code-only Docker context controls, and complete OCI image
  labels.

Earlier release notes are retained in [docs/CHANGELOG.md](docs/CHANGELOG.md).

## [5.0.3] - 2026-07-12

### Security

- Bound corpus-ingest download deadlines and archive expansion, including
  artifact-specific deadlines for release downloads, to prevent unbounded
  ingestion work from consuming service resources.
- Pinned all GitHub Actions used by CI and release workflows to immutable SHA
  revisions.
