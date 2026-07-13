# Changelog

All notable changes to GeneReviews-Link are documented in this file.

## [Unreleased]

## [5.0.4] - 2026-07-13

### Changed

- **The corpus is now declared and implemented as a `restored-database`, not an
  `external-reference`.** It is what the code always did -- `pg_dump -Fc` out,
  `pg_restore` in -- but it was declared as a file the server reads, and the restore ran
  inside the request-serving process, over the network, as the database owner.
- The corpus ships as an immutable, attested, **data-only** artifact
  (`corpus-data-2026-07-13-r1`,
  `sha256:4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc`). The previous
  release was a schema-bearing dump: its table of contents carried `SCHEMA`, `TABLE`,
  `INDEX`, `CONSTRAINT`, `FK CONSTRAINT` and `EXTENSION` entries, so restoring it executed
  DDL that arrived over the network. The new artifact contains **only** `TABLE DATA` for
  four named tables. Same corpus (882 chapters, 40,853 passages), different envelope.
- A new no-egress `genereview-corpus-restore` init sidecar is the only path by which
  corpus data enters PostgreSQL. It is on an internal-only network (it can reach the
  database and nothing else), proves the artifact against a digest committed in this
  repository before opening it, rejects any archive entry that is not table data for a
  named corpus table, rejects a plain-SQL script on its magic bytes, and restores as an
  unprivileged `NOSUPERUSER` role under
  `--no-owner --no-privileges --single-transaction --exit-on-error`. Schema and indexes
  come only from the reviewed in-repo migrations.
- **The serving application has no restore path at all.** It no longer downloads a bundle,
  no longer runs `pg_restore`, and no longer needs egress to a release host.
- Harden the `postgres` sidecar to the full standard: read-only rootfs, `cap_drop: [ALL]`,
  `no-new-privileges`, bounded resources and logging, no published ports, digest-pinned
  untagged image, and an internal-only network.
- Adopt the GeneFoundry router container-release standard with SHA-pinned reusable
  container CI/release callers, digest-only production image configuration, code-only
  Docker context controls, and complete OCI image labels.

### Fixed

- The production image stripped `numpy`, which `pgvector.asyncpg` imports at module import
  time. Every database connection raised `ModuleNotFoundError`, so the whole corpus path
  was dead in the production target. numpy is retained; only its bundled test-data trees
  are removed (the fleet OCI content policy denies any `data`/`corpus` path component).
- The production image stripped `genereview_link.corpus`, which the server imported at
  startup for `BGE_MODEL_NAME`/`BGE_DIM` -- the app exited on boot. Those constants moved
  to `genereview_link.retrieval.model_identity`, which the serving image ships.
- `TMPDIR` pointed at `/tmp/genereview-link`, a directory the `/tmp` tmpfs mount hides at
  runtime. It is now `/tmp`.
- `postgres` starts as its own `999:999` rather than as root. The stock entrypoint drops to
  the postgres user with `gosu`, which needs `CAP_SETUID`/`CAP_SETGID`, and chowns `PGDATA`,
  which needs `CAP_CHOWN` -- both impossible under the mandatory `cap_drop: [ALL]`, where
  the container died with "operation not permitted".

Earlier release notes are retained in [docs/CHANGELOG.md](docs/CHANGELOG.md).

## [5.0.3] - 2026-07-12

### Security

- Bound corpus-ingest download deadlines and archive expansion, including
  artifact-specific deadlines for release downloads, to prevent unbounded
  ingestion work from consuming service resources.
- Pinned all GitHub Actions used by CI and release workflows to immutable SHA
  revisions.
