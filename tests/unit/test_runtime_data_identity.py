"""Guards for the GeneFoundry runtime data identity (v1) this deployment publishes.

The fleet controller activates a new data release only for a service whose `/health`
proves which reviewed release it is serving, and whose read-only probe can be exec'd to
observe the data independently of that claim. These tests pin the exact published shape,
the equal/unequal verdict, the probe's output contract and determinism, and the two places
the pinned identity must not be able to drift apart (compose default vs
container-release.json).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from genereview_link.config import ServerConfig
from genereview_link.data_probe import DataProbeError, _bare_schema_version, probe
from genereview_link.runtime_data_identity import (
    SCHEMA_VERSION,
    RuntimeDataIdentityError,
    canonical_digest,
    canonical_release_tag,
    configured_data_identity,
    observed_data_identity,
    record_data_identity,
    release_identity_payload,
)
from genereview_link.server_manager import UnifiedServerManager

ROOT = Path(__file__).resolve().parents[2]
RELEASE_TAG = "corpus-data-2026-07-13-r1"
SEED_DIGEST = "sha256:4486e499337e9f816a2aa0741f2a0e51ca38cda52f96fb57564cfc36f4b3c5bc"
DUMP_DIGEST = "sha256:449296ee8bfc9f032c5df3ad0f4d12a8f4a280776d5bf1dbeaecf7709a771e99"
COUNTS = {"chapters": 882, "passages": 40853, "embeddings": 40853}


class _Config:
    """The corpus-identity slice of `settings` the contract reads."""

    def __init__(self, tag: str = RELEASE_TAG, bundle: str = SEED_DIGEST, dump: str = "") -> None:
        self.CORPUS_RELEASE_TAG = tag
        self.CORPUS_BUNDLE_SHA256 = bundle
        self.CORPUS_DUMP_SHA256 = dump


class _Pool:
    """A minimal asyncpg-shaped stand-in over one in-memory corpus."""

    def __init__(self, corpus_version: str = "2026-05-10-r6", **counts: int) -> None:
        self.corpus_version = corpus_version
        self.counts = {**COUNTS, **counts}
        self.row: dict[str, Any] | None = None
        self.executed: list[tuple[Any, ...]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self.corpus_version if "genereview_corpus_version" in query else None

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "genereview_runtime_data_identity" in query:
            return self.row
        return dict(self.counts)

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append(args)
        self.row = {
            "release_tag": args[0],
            "digest": args[1],
            "corpus_version": args[3],
            "counts": args[5],
        }


def _manifest(**overrides: Any) -> dict[str, Any]:
    """The identity-bearing subset of the pinned release's manifest.json (v2)."""
    return {
        "corpus_release_id": RELEASE_TAG,
        "corpus_version": "2026-05-10-r6",
        "chapter_count": COUNTS["chapters"],
        "passage_count": COUNTS["passages"],
        "embedding": {"count": COUNTS["embeddings"]},
        "checksums": {"corpus.dump": DUMP_DIGEST.removeprefix("sha256:")},
        **overrides,
    }


def _health(**state: Any) -> dict[str, Any]:
    app = UnifiedServerManager().create_fastapi_app(ServerConfig(transport="http"))
    for name, value in state.items():
        setattr(app.state, name, value)
    return TestClient(app, raise_server_exceptions=True).get("/health").json()  # type: ignore[no-any-return]


# --- the published contract -------------------------------------------------------


def test_health_publishes_the_controller_contract_when_identity_matches() -> None:
    identity = {"release_tag": RELEASE_TAG, "digest": SEED_DIGEST}
    body = _health(
        corpus_version="2026-05-10-r6",
        release_identity=release_identity_payload(identity, identity),
        data_available=True,
    )
    assert body["data_available"] is True
    assert body["release_identity"]["schema_version"] == 1 == SCHEMA_VERSION
    pair = body["release_identity"]["data_identity"]
    assert set(pair["expected"]) == set(pair["actual"]) == {"release_tag", "digest"}
    assert pair["actual"] == pair["expected"] == identity
    assert body["status"] == "healthy"


def test_health_is_not_available_and_degrades_when_the_identities_disagree() -> None:
    expected = {"release_tag": RELEASE_TAG, "digest": SEED_DIGEST}
    actual = {"release_tag": "corpus-data-2026-05-10-r1", "digest": SEED_DIGEST}
    body = _health(
        corpus_version="2026-05-10-r6",
        release_identity=release_identity_payload(expected, actual),
        data_available=False,
    )
    assert body["data_available"] is False
    assert (
        body["release_identity"]["data_identity"]["actual"]
        != (body["release_identity"]["data_identity"]["expected"])
    )
    assert body["status"] == "degraded"


def test_health_keeps_the_envelope_shape_when_no_lifespan_ever_ran() -> None:
    """Absence of state is reported as absence, never as a fabricated identity."""
    body = _health()
    assert body["data_available"] is False
    assert body["release_identity"] == {
        "schema_version": 1,
        "data_identity": {"expected": None, "actual": None},
    }
    assert body["status"] == "healthy"  # no corpus at all: /passages already 503s


# --- expected: what the deployment is configured for ------------------------------


def test_configured_identity_is_the_pinned_release_and_seed_digest() -> None:
    assert configured_data_identity(_Config()) == {
        "release_tag": RELEASE_TAG,
        "digest": SEED_DIGEST,
    }


def test_configured_identity_prefers_the_direct_dump_digest() -> None:
    dump = "b" * 64
    assert configured_data_identity(_Config(dump=dump))["digest"] == f"sha256:{dump}"


@pytest.mark.parametrize("digest", ["", "0" * 64, "f" * 64, "not-a-digest"])
def test_configured_identity_refuses_a_missing_or_placeholder_digest(digest: str) -> None:
    with pytest.raises(RuntimeDataIdentityError):
        configured_data_identity(_Config(bundle=digest))


@pytest.mark.parametrize("tag", ["", "latest", "2026-07-13-r1", "corpus-data-2026-07-13"])
def test_configured_identity_refuses_a_mutable_or_malformed_release_tag(tag: str) -> None:
    with pytest.raises(RuntimeDataIdentityError):
        canonical_release_tag(tag)


def test_canonical_digest_normalises_case_and_the_sha256_prefix() -> None:
    assert canonical_digest("SHA256:" + "A" * 64, label="x") == "sha256:" + "a" * 64


# --- actual: what the restore materialised ----------------------------------------


async def test_recording_binds_the_artifact_to_the_rows_that_are_really_present() -> None:
    pool = _Pool()
    recorded = await record_data_identity(
        pool,
        release_tag=RELEASE_TAG,
        digest=SEED_DIGEST,
        seed_mode="legacy",
        manifest=_manifest(),
    )
    assert recorded == {"release_tag": RELEASE_TAG, "digest": SEED_DIGEST}
    assert await observed_data_identity(pool) == recorded


@pytest.mark.parametrize(
    "overrides",
    [
        {"corpus_version": "2026-05-10-r5"},
        {"passage_count": 40852},
        {"corpus_release_id": "corpus-data-2026-05-10-r1"},
    ],
)
async def test_recording_refuses_an_artifact_that_is_not_the_restored_corpus(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(RuntimeDataIdentityError):
        await record_data_identity(
            _Pool(),
            release_tag=RELEASE_TAG,
            digest=SEED_DIGEST,
            seed_mode="legacy",
            manifest=_manifest(**overrides),
        )


async def test_observation_refuses_a_corpus_swapped_under_the_recorded_identity() -> None:
    pool = _Pool()
    await record_data_identity(
        pool,
        release_tag=RELEASE_TAG,
        digest=SEED_DIGEST,
        seed_mode="legacy",
        manifest=_manifest(),
    )
    pool.counts["passages"] = COUNTS["passages"] - 1
    with pytest.raises(RuntimeDataIdentityError):
        await observed_data_identity(pool)


async def test_observation_reports_absence_rather_than_guessing() -> None:
    with pytest.raises(RuntimeDataIdentityError):
        await observed_data_identity(_Pool())


# --- the read-only probe ------------------------------------------------------------


class _ProbeConnection:
    """Records every statement so the probe can be proven read-only and snapshot-bound."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.transactions: list[dict[str, Any]] = []
        self.closed = False

    def transaction(self, **kwargs: Any) -> Any:
        self.transactions.append(kwargs)
        connection = self

        class _Transaction:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: object) -> bool:
                return False

        assert connection is not None
        return _Transaction()

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.statements.append(query)
        return self.rows[len(self.statements) - 1]

    async def close(self) -> None:
        self.closed = True


async def test_probe_prints_exactly_the_three_controller_keys(monkeypatch: Any) -> None:
    connection = _ProbeConnection(["genereview:0007_embedding_run_identity", 40853, "NBK1116:0"])
    import asyncpg

    async def _connect(dsn: str) -> _ProbeConnection:
        assert dsn == "postgresql://x/y"
        return connection

    monkeypatch.setattr(asyncpg, "connect", _connect)
    observation = await probe("postgresql://x/y")

    assert set(observation) == {"data_schema_version", "record_count", "query_result_sha256"}
    assert observation["data_schema_version"] == "0007_embedding_run_identity"
    assert observation["record_count"] == 40853
    assert re.fullmatch(r"[0-9a-f]{64}", observation["query_result_sha256"])
    # Deterministic: the digest is over the UTF-8 text of the canonical first passage id.
    import hashlib

    assert observation["query_result_sha256"] == hashlib.sha256(b"NBK1116:0").hexdigest()
    # Read-only and snapshot-bound; nothing is written and the connection is released.
    assert connection.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert all(
        statement.lstrip().lower().startswith("select") for statement in connection.statements
    )
    assert connection.closed is True


async def test_probe_is_deterministic_for_one_data_release(monkeypatch: Any) -> None:
    import asyncpg

    async def _connect(dsn: str) -> _ProbeConnection:
        return _ProbeConnection(["genereview:0007_embedding_run_identity", 40853, "NBK1116:0"])

    monkeypatch.setattr(asyncpg, "connect", _connect)
    first = await probe("postgresql://x/y")
    second = await probe("postgresql://x/y")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


async def test_probe_refuses_an_empty_corpus(monkeypatch: Any) -> None:
    import asyncpg

    async def _connect(dsn: str) -> _ProbeConnection:
        return _ProbeConnection(["genereview:0007_embedding_run_identity", 0, None])

    monkeypatch.setattr(asyncpg, "connect", _connect)
    with pytest.raises(DataProbeError):
        await probe("postgresql://x/y")


def test_probe_reports_the_bare_data_schema_version() -> None:
    assert _bare_schema_version("genereview:0007_embedding_run_identity") == (
        "0007_embedding_run_identity"
    )
    with pytest.raises(DataProbeError):
        _bare_schema_version(None)


# --- the pinned identity must not be able to drift ----------------------------------


class _TolerantLoader(yaml.SafeLoader):
    """Compose custom tags (`!reset`, `!override`) are not YAML this loader must resolve."""


_TolerantLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
    ),
)


def _compose() -> dict[str, Any]:
    """Load the Compose file the fleet controller deploys, tolerating its custom tags."""
    text = (ROOT / "docker/docker-compose.yml").read_text()
    return yaml.load(text, Loader=_TolerantLoader)  # noqa: S506 - _TolerantLoader IS SafeLoader


def _release_config() -> dict[str, Any]:
    return json.loads((ROOT / "container-release.json").read_bytes())  # type: ignore[no-any-return]


def test_compose_default_release_tag_equals_the_pinned_data_release() -> None:
    """An UNCHANGED server .env.docker must render the reviewed release, not an empty one."""
    compose = _compose()
    pinned = _release_config()["data"]["release_tag"]
    for service in ("genereview-link", "genereview-corpus-restore"):
        assert compose["services"][service]["environment"]["CORPUS_RELEASE_TAG"] == (
            "${CORPUS_RELEASE_TAG:-" + pinned + "}"
        )


def test_the_server_reads_the_seed_digest_it_republishes_as_expected() -> None:
    environment = _compose()["services"]["genereview-link"]["environment"]
    assert environment["CORPUS_BUNDLE_SHA256"] == "${CORPUS_BUNDLE_SHA256:-}"
    assert environment["CORPUS_DUMP_SHA256"] == "${CORPUS_DUMP_SHA256:-}"


def test_release_config_declares_the_adopted_contract_and_the_probe_schema_version() -> None:
    data = _release_config()
    assert data["data_identity_contract"] == "runtime-v1"
    assert data["data"]["schema_compatibility"] == ["0007_embedding_run_identity"]


def test_the_new_control_migration_is_reviewed_and_allowlisted() -> None:
    from genereview_link.corpus.schema_identity import EXPECTED_CONTROL_MIGRATIONS

    migration = "0008_runtime_data_identity"
    assert migration in EXPECTED_CONTROL_MIGRATIONS
    assert (ROOT / "genereview_link/db/migrations/control" / f"{migration}.sql").is_file()
    assert any(
        path.endswith(f"db/migrations/control/{migration}.sql")
        for path in _release_config()["data"]["image_allowlist"]
    )
