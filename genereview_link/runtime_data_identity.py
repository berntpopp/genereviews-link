"""The GeneFoundry runtime data identity (v1) for the restored GeneReviews corpus.

The fleet controller (``strato_v6_docker_npm``) can only activate a new data release for a
service that can *prove*, at runtime, which reviewed data release it is serving. ``/health``
publishes that proof::

    "data_available": true,
    "release_identity": {
      "schema_version": 1,
      "data_identity": {
        "expected": {"release_tag": "...", "digest": "sha256:..."},
        "actual":   {"release_tag": "...", "digest": "sha256:..."}
      }
    }

``expected`` is what this deployment is *configured* for: ``CORPUS_RELEASE_TAG`` and the
seed-artifact digest the init sidecar is required to prove -- exactly
``container-release.json`` ``.data.release_tag`` / ``.data.digest``.

``actual`` is what is *restored*. It is NOT the configuration read back. The no-egress init
sidecar proves the staged artifact byte-for-byte, matches the artifact manifest's corpus
identity (``corpus_version`` and the chapter/passage/embedding counts) against the rows
that are really in the database, and only then writes
``public.genereview_runtime_data_identity``. The serving process re-derives those same live
facts before it republishes the row, so a database swapped underneath a running deployment
stops matching and ``data_available`` goes false.

This is the GeneReviews analogue of the file-based identity manifest used by the
file-backed services in the fleet (``clingen-link``, ``clinvar-link``): the corpus here is a
restored database, so its identity lives in a control table rather than beside a file, and
the "verify against the bytes on open" step is the live re-derivation below.

Nothing here restores, downloads, or writes corpus data; the serving process has no restore
path by design (#97).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "COUNT_KEYS",
    "SCHEMA_VERSION",
    "RuntimeDataIdentityError",
    "canonical_digest",
    "canonical_release_tag",
    "configured_data_identity",
    "health_release_identity",
    "live_corpus_facts",
    "observed_data_identity",
    "record_data_identity",
    "release_identity_payload",
]

#: Version of the published ``release_identity`` envelope. The controller requires 1.
SCHEMA_VERSION = 1

#: The counted entities that bind a recorded identity to the live database.
COUNT_KEYS = ("chapters", "passages", "embeddings")

_RELEASE_TAG = re.compile(r"^corpus-data-20[0-9]{2}-[0-9]{2}-[0-9]{2}-r[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Digests that look like verification while verifying nothing. Refused, exactly as
#: ``db.restore.normalize_corpus_digest`` refuses them on the init side -- the serving
#: process must never republish a placeholder as an identity.
_PLACEHOLDER_DIGESTS = frozenset({"0" * 64, "f" * 64, hashlib.sha256(b"").hexdigest()})

_COUNT_QUERY = (
    "select (select count(*) from genereview.genereview_chapters) as chapters, "
    "(select count(*) from genereview.genereview_passages) as passages, "
    "(select count(*) from genereview.genereview_embeddings_bge384) as embeddings"
)


class RuntimeDataIdentityError(RuntimeError):
    """The deployment cannot prove which reviewed data release it is serving."""


def canonical_release_tag(value: object) -> str:
    """Return the exact immutable corpus data release tag, or fail closed."""
    if not isinstance(value, str) or not _RELEASE_TAG.fullmatch(value.strip()):
        raise RuntimeDataIdentityError(
            "an exact corpus data release tag (corpus-data-YYYY-MM-DD-rN) is required"
        )
    return value.strip()


def canonical_digest(value: object, *, label: str) -> str:
    """Return ``sha256:<64 hex>`` for *value*, refusing placeholders."""
    if not isinstance(value, str):
        raise RuntimeDataIdentityError(f"an exact 64-character {label} is required")
    normalized = value.strip().lower().removeprefix("sha256:")
    if not _SHA256.fullmatch(normalized):
        raise RuntimeDataIdentityError(f"an exact 64-character {label} is required")
    if normalized in _PLACEHOLDER_DIGESTS:
        raise RuntimeDataIdentityError(f"the configured {label} is a placeholder, not an identity")
    return f"sha256:{normalized}"


def configured_data_identity(settings: Any) -> dict[str, str]:
    """Return the data release this deployment is configured for (``expected``).

    The digest is the seed artifact the init sidecar must prove: the corpus bundle in
    legacy mode, ``corpus.dump`` in direct mode. Either way it is the value published as
    ``container-release.json`` ``.data.digest``, so ``expected`` is the manifest's own data
    identity rather than a second, drifting copy of it.
    """
    return {
        "release_tag": canonical_release_tag(settings.CORPUS_RELEASE_TAG),
        "digest": canonical_digest(
            settings.CORPUS_DUMP_SHA256.strip() or settings.CORPUS_BUNDLE_SHA256,
            label="corpus seed artifact SHA-256",
        ),
    }


async def live_corpus_facts(pool: Any) -> dict[str, Any]:
    """Re-derive, from the database itself, which corpus is active and how large it is."""
    version = await pool.fetchval(
        "select version from public.genereview_corpus_version where is_active"
    )
    if not isinstance(version, str) or not version:
        raise RuntimeDataIdentityError("no active corpus version is present")
    row = await pool.fetchrow(_COUNT_QUERY)
    if row is None:
        raise RuntimeDataIdentityError("the restored corpus counts are unavailable")
    return {"corpus_version": version, "counts": {name: int(row[name]) for name in COUNT_KEYS}}


def _manifest_counts(manifest: Mapping[str, object]) -> dict[str, int]:
    embedding = manifest.get("embedding")
    values = {
        "chapters": manifest.get("chapter_count"),
        "passages": manifest.get("passage_count"),
        "embeddings": embedding.get("count") if isinstance(embedding, Mapping) else None,
    }
    if any(type(value) is not int or value <= 0 for value in values.values()):
        raise RuntimeDataIdentityError("the artifact manifest has no usable corpus counts")
    return {name: int(value) for name, value in values.items()}  # type: ignore[arg-type]


def _manifest_dump_digest(manifest: Mapping[str, object]) -> str:
    checksums = manifest.get("checksums")
    value = checksums.get("corpus.dump") if isinstance(checksums, Mapping) else None
    return canonical_digest(value, label="artifact corpus.dump SHA-256")


def _manifest_claims_release(manifest: Mapping[str, object], release_tag: str) -> bool:
    """Whether the artifact says it IS this release.

    Manifest v2 records the full tag in ``corpus_release_id``; v3 records the bare
    ``YYYY-MM-DD-rN`` id. Both are accepted, and only for the configured tag.
    """
    claimed = manifest.get("corpus_release_id")
    return isinstance(claimed, str) and claimed in {
        release_tag,
        release_tag.removeprefix("corpus-data-"),
    }


async def record_data_identity(
    pool: Any,
    *,
    release_tag: str,
    digest: str,
    seed_mode: str,
    manifest: Mapping[str, object],
) -> dict[str, str]:
    """Bind a proven seed artifact to the corpus that is really in the database.

    *digest* must have been computed from the artifact's own bytes by the caller, never
    copied from configuration. The manifest's corpus identity is then required to equal the
    live rows, so recording an identity for a database that holds some *other* corpus is
    impossible.

    Raises:
        RuntimeDataIdentityError: the artifact is not the configured release, or the
            restored database is not the artifact's corpus.
    """
    if seed_mode not in {"legacy", "direct"}:
        raise RuntimeDataIdentityError("seed mode must be legacy or direct")
    release = canonical_release_tag(release_tag)
    seed_digest = canonical_digest(digest, label="proven corpus seed artifact SHA-256")
    if not _manifest_claims_release(manifest, release):
        raise RuntimeDataIdentityError("the staged artifact is not the configured data release")
    dump_digest = _manifest_dump_digest(manifest)
    corpus_version = manifest.get("corpus_version")
    counts = _manifest_counts(manifest)

    facts = await live_corpus_facts(pool)
    if facts["corpus_version"] != corpus_version:
        raise RuntimeDataIdentityError("the restored corpus is not the artifact's corpus version")
    if facts["counts"] != counts:
        raise RuntimeDataIdentityError("the restored corpus does not have the artifact's counts")

    await pool.execute(
        "insert into public.genereview_runtime_data_identity "
        "(identity_key, release_tag, digest, seed_mode, corpus_version, dump_digest, counts) "
        "values (true, $1, $2, $3, $4, $5, $6::jsonb) "
        "on conflict (identity_key) do update set "
        "release_tag = excluded.release_tag, digest = excluded.digest, "
        "seed_mode = excluded.seed_mode, corpus_version = excluded.corpus_version, "
        "dump_digest = excluded.dump_digest, counts = excluded.counts, recorded_at = now()",
        release,
        seed_digest,
        seed_mode,
        facts["corpus_version"],
        dump_digest,
        _counts_json(counts),
    )
    return {"release_tag": release, "digest": seed_digest}


def _counts_json(counts: Mapping[str, int]) -> str:
    return json.dumps(dict(sorted(counts.items())), separators=(",", ":"))


async def observed_data_identity(pool: Any) -> dict[str, str]:
    """Return the identity of the corpus that is actually restored (``actual``).

    The recorded row is only republished after the live database is re-derived and found to
    still be the corpus it was recorded for. A volume swapped for a different corpus, or a
    corpus mutated underneath the deployment, fails here instead of being reported as the
    reviewed release.

    Raises:
        RuntimeDataIdentityError: no identity was recorded, or it no longer describes the
            data this process is serving.
    """
    row = await pool.fetchrow(
        "select release_tag, digest, corpus_version, counts::text as counts "
        "from public.genereview_runtime_data_identity where identity_key"
    )
    if row is None:
        raise RuntimeDataIdentityError(
            "no runtime data identity was recorded; the genereview-corpus-restore init "
            "sidecar has not adopted this corpus"
        )
    recorded_counts = json.loads(row["counts"])
    facts = await live_corpus_facts(pool)
    if facts["corpus_version"] != row["corpus_version"]:
        raise RuntimeDataIdentityError(
            "the active corpus is not the one the identity was recorded for"
        )
    if facts["counts"] != recorded_counts:
        raise RuntimeDataIdentityError("the restored corpus no longer has its recorded counts")
    return {
        "release_tag": canonical_release_tag(row["release_tag"]),
        "digest": canonical_digest(row["digest"], label="recorded corpus seed artifact SHA-256"),
    }


def release_identity_payload(
    expected: Mapping[str, str] | None, actual: Mapping[str, str] | None
) -> dict[str, Any]:
    """Build the exact ``release_identity`` envelope ``/health`` publishes."""
    return {
        "schema_version": SCHEMA_VERSION,
        "data_identity": {
            "expected": dict(expected) if expected is not None else None,
            "actual": dict(actual) if actual is not None else None,
        },
    }


def health_release_identity(state: Any) -> tuple[dict[str, Any], bool]:
    """Return the ``(release_identity, data_available)`` pair ``/health`` publishes.

    Mirrors ``corpus_health``: an app assembled outside the normal server lifespan (unit
    tests, embedded use) reports absence rather than fabricating an identity it never
    resolved.
    """
    payload = getattr(state, "release_identity", None)
    if not isinstance(payload, dict):
        payload = release_identity_payload(None, None)
    return payload, bool(getattr(state, "data_available", False))
