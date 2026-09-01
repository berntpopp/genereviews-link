"""Regressions for production readiness and privileged bootstrap review findings."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import genereview_link.corpus.readiness as readiness
import genereview_link.corpus.release_assets as release_assets
from genereview_link.corpus.handoff import HandoffError, _load_json
from genereview_link.corpus.release_assets import (
    AssetIdentity,
    ReleaseAssetError,
    ReleaseIdentity,
)

ROOT = Path(__file__).resolve().parents[2]


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


def _inline_bootstrap() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/corpus-data-release.yml").read_text())
    step = next(
        item
        for item in workflow["jobs"]["publish"]["steps"]
        if item.get("name") == "Fetch durable digest-addressed sealed handoff"
    )
    script = step["run"]
    return script.split("python3 -I - <<'PY'\n", 1)[1].split("\nPY", 1)[0]


def _locator_raw(
    *, duplicate: bool = False, value: str = '"genereviews-handoff-locator-v1"'
) -> str:
    names = [
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "seal-manifest.json",
        "genereviews_link-5.1.6-py3-none-any.whl",
    ]
    assets = [
        {
            "name": name,
            "url": f"https://api.github.com/repos/owner/seals/releases/assets/{index}",
            "sha256": hashlib.sha256(b"owned").hexdigest(),
            "size_bytes": len(b"owned"),
        }
        for index, name in enumerate(names, 1)
    ]
    tail = json.dumps(
        {
            "object_id": "1" * 64,
            "build_revision": "2" * 40,
            "assets": assets,
        },
        separators=(",", ":"),
    )[1:]
    duplicate_field = ',"format":"shadow"' if duplicate else ""
    return f'{{"format":{value}{duplicate_field},{tail}'


def _run_inline(
    tmp_path: Path, raw: str, *, source: str | None = None
) -> subprocess.CompletedProcess[str]:
    destination = tmp_path / "handoff"
    destination.mkdir(mode=0o700, exist_ok=True)
    return subprocess.run(  # noqa: S603 - reviewed inline workflow is the test subject
        [sys.executable, "-I", "-c", source or _inline_bootstrap()],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HANDOFF_OBJECT": str(destination),
            "GENEREVIEWS_HANDOFF_LOCATOR": raw,
            "GENEREVIEWS_HANDOFF_REPOSITORIES": "owner/seals",
            "OBJECT_ID": "1" * 64,
            "GITHUB_SHA": "2" * 40,
            "GH_TOKEN": "",
        },
    )


@pytest.mark.parametrize(
    "raw",
    [
        _locator_raw(duplicate=True),
        _locator_raw(value="NaN"),
        "[" * 10_000 + "]" * 10_000 + "\n",
    ],
    ids=("duplicate", "nonfinite", "deep"),
)
def test_actual_privileged_inline_bootstrap_uses_strict_bounded_json(
    tmp_path: Path, raw: str
) -> None:
    result = _run_inline(tmp_path, raw)
    assert result.returncode != 0
    assert "handoff locator is not strict bounded JSON" in result.stderr
    assert "RecursionError" not in result.stderr


def test_exact_bundle_json_loader_rejects_duplicate_nonfinite_and_deep_json(
    tmp_path: Path,
) -> None:
    for index, raw in enumerate(
        (b'{"x":1,"x":2}', b'{"x":NaN}', b"[" * 10_000 + b"]" * 10_000 + b"\n")
    ):
        path = tmp_path / f"manifest-{index}.json"
        path.write_bytes(raw)
        with pytest.raises(HandoffError, match="invalid JSON"):
            _load_json(path)


def test_privileged_inline_cleanup_preserves_substituted_file(tmp_path: Path) -> None:
    source = _inline_bootstrap()
    replacement = """
class _Response:
    calls = 0
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, size):
        del size
        self.calls += 1
        if self.calls == 1: return b"owned"
        target = Path(os.environ["HANDOFF_OBJECT"]) / "corpus.dump"
        target.unlink()
        target.write_bytes(b"foreign")
        raise OSError("forced substitution")
class _Opener:
    def open(self, *args, **kwargs): return _Response()
opener = _Opener()
""".strip()
    source = source.replace("opener = build_opener(TrustedRedirects())", replacement)
    destination = tmp_path / "handoff"
    result = _run_inline(tmp_path, _locator_raw(), source=source)

    assert result.returncode != 0
    assert (destination / "corpus.dump").read_bytes() == b"foreign"


@pytest.mark.asyncio
async def test_release_asset_cleanup_preserves_close_to_stat_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = release_assets.PUBLICATION_ASSET_NAMES
    identities = tuple(
        AssetIdentity(
            asset_id=index,
            name=name,
            size=len(b"owned"),
            digest="sha256:" + hashlib.sha256(b"owned").hexdigest(),
            url=f"https://api.github.com/repos/owner/repo/releases/assets/{index}",
        )
        for index, name in enumerate(names, 1)
    )

    async def identity(*_args: object, **_kwargs: object) -> ReleaseIdentity:
        return ReleaseIdentity(17, "corpus-data-2026-08-30-r1", "a" * 40, identities)

    async def swapped_stream(_client: object, _url: str, target: Path, **kwargs: object) -> str:
        target.write_bytes(b"owned")
        output = kwargs.get("created_identity")
        if isinstance(output, list):
            info = target.stat(follow_symlinks=False)
            output.append((info.st_dev, info.st_ino))
        target.unlink()
        target.write_bytes(b"foreign")
        return hashlib.sha256(b"owned").hexdigest()

    monkeypatch.setattr(release_assets, "_release_assets", identity)
    monkeypatch.setattr(release_assets, "stream_to_file", swapped_stream)
    destination = tmp_path / "release"
    destination.mkdir()

    with pytest.raises(ReleaseAssetError):
        await release_assets.download_release_assets(
            "owner/repo",
            "corpus-data-2026-08-30-r1",
            destination,
            release_id=17,
        )
    assert (destination / names[0]).read_bytes() == b"foreign"
