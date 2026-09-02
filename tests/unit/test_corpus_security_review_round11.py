"""Regressions for transactional release readiness and the bundle metadata parser.

The privileged inline bootstrap this round also covered lived in the deleted
publisher workflow; the strict-JSON policy it enforced is now asserted directly
against the loader every consumer of a published bundle actually calls.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import genereview_link.corpus.readiness as readiness
from genereview_link.corpus.bundle_integrity import BundleIntegrityError, _load_json


def test_transactional_readiness_requires_exact_content_and_computation_identity() -> None:
    with pytest.raises(readiness.ReadinessError, match="content identity"):
        readiness.assert_runtime_manifest_identity(
            {"content_identity": {"digest": "expected"}, "computation": {"run": "same"}},
            content_identity={"digest": "different"},
            computation={"run": "same"},
        )
    with pytest.raises(readiness.ReadinessError, match="computation"):
        readiness.assert_runtime_manifest_identity(
            {"content_identity": {"digest": "same"}, "computation": {"run": "expected"}},
            content_identity={"digest": "same"},
            computation={"run": "different"},
        )

    writer = inspect.getsource(readiness.write_release_readiness)
    assert "collect_content_identity(connection)" in writer
    assert "load_active_computation(connection)" in writer
    assert writer.index("assert_runtime_manifest_identity") < writer.index(
        "insert into public.genereview_release_readiness"
    )


@pytest.mark.asyncio
async def test_readiness_rejects_runtime_identity_before_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Context:
        def __init__(self, value: object) -> None:
            self.value = value

        async def __aenter__(self) -> object:
            return self.value

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Connection:
        def transaction(self) -> Context:
            return Context(None)

        async def execute(self, statement: str, *_args: object) -> None:
            statements.append(statement)

        async def fetchval(self, statement: str, *_args: object) -> object:
            return True if "to_regclass" in statement else None

        async def fetchrow(self, statement: str, *_args: object) -> dict[str, object]:
            if "genereview_corpus_version" in statement:
                return {"version": "2026-08-30", "tarball_sha256": "a" * 64}
            return {"chapters": 1, "passages": 1, "embeddings": 1}

        async def fetch(self, *_args: object) -> list[object]:
            return []

    connection = Connection()

    class Pool:
        def acquire(self) -> Context:
            return Context(connection)

    async def content(_connection: object) -> dict[str, object]:
        return {"digest": "restored"}

    async def computation(_connection: object) -> dict[str, object]:
        return {"run": "sealed"}

    monkeypatch.setattr(readiness, "collect_content_identity", content)
    monkeypatch.setattr(readiness, "load_active_computation", computation)

    with pytest.raises(readiness.ReadinessError, match="content identity"):
        await readiness.write_release_readiness(
            Pool(),
            {
                "corpus_version": "2026-08-30",
                "content_identity": {"digest": "sealed"},
                "computation": {"run": "sealed"},
            },
            artifact_digest="sha256:" + "b" * 64,
            manifest_digest="sha256:" + "c" * 64,
            checksums_digest="sha256:" + "d" * 64,
            release_tag="corpus-data-2026-08-30-r1",
        )

    assert not any("insert into public.genereview_release_readiness" in item for item in statements)


def test_exact_bundle_json_loader_rejects_duplicate_nonfinite_and_deep_json(
    tmp_path: Path,
) -> None:
    for index, raw in enumerate(
        (b'{"x":1,"x":2}', b'{"x":NaN}', b"[" * 10_000 + b"]" * 10_000 + b"\n")
    ):
        path = tmp_path / f"manifest-{index}.json"
        path.write_bytes(raw)
        with pytest.raises(BundleIntegrityError, match="invalid JSON"):
            _load_json(path)
