"""Regressions for exact-head verifier and finite-value review findings."""

from __future__ import annotations

import hashlib
import io
import json
import math
import tarfile
from pathlib import Path

import pytest
import yaml

import genereview_link.corpus.handoff_locator as handoff_locator
import genereview_link.corpus.rights_locator as rights_locator
import genereview_link.corpus.source_locator as source_locator
from genereview_link.corpus.evaluation import EvaluationRejectedError, assert_evaluation_accepted
from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed
from genereview_link.db.restore import ArchivePolicyError, extract_bundle
from genereview_link.strict_json import StrictJsonError, load_strict_json

ROOT = Path(__file__).resolve().parents[2]


def test_external_verifier_passes_complete_independently_hashed_readiness_tuple() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    steps = workflow["jobs"]["verify"]["steps"]
    script = next(
        step["run"] for step in steps if "Verify exact manifest counts" in str(step.get("name", ""))
    )
    call = script.split("await write_release_readiness(", 1)[1].split(")", 1)[0]

    assert 'release_tag=os.environ["EXPECTED_RELEASE_TAG"]' in call
    assert 'artifact_digest="sha256:" + dump_sha256' in call
    assert 'manifest_digest="sha256:" + manifest_sha256' in call
    assert 'checksums_digest="sha256:" + checksums_sha256' in call
    for name in ("corpus.dump", "manifest.json", "SHA256SUMS"):
        assert f'root / "{name}"' in script
    assert "hashlib.file_digest" in script


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_strict_json_rejects_nonstandard_nonfinite_constants(constant: bytes) -> None:
    with pytest.raises(StrictJsonError):
        load_strict_json(b'{"value":' + constant + b"}", max_bytes=1024)


def _direct_seed(root: Path, manifest: bytes) -> tuple[str, str, str]:
    root.mkdir()
    dump = b"PGDMP-data"
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    (root / "corpus.dump").write_bytes(dump)
    (root / "manifest.json").write_bytes(manifest)
    (root / "SHA256SUMS").write_bytes(sums)
    return dump_digest, manifest_digest, hashlib.sha256(sums).hexdigest()


def test_direct_manifest_maps_nan_to_domain_parse_error(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed, b'{"corpus_release_id":"x","value":NaN}\n')
    with pytest.raises(DirectSeedError, match="not valid JSON"):
        extract_direct_seed(
            seed,
            tmp_path / "restore",
            expected_dump_sha256=anchors[0],
            expected_manifest_sha256=anchors[1],
            expected_checksums_sha256=anchors[2],
        )


def test_legacy_manifest_maps_infinity_to_domain_parse_error(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        manifest = b'{"checksums":{},"value":Infinity}\n'
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        bundle.addfile(info, io.BytesIO(manifest))
    with pytest.raises(ArchivePolicyError, match="not valid bounded JSON"):
        extract_bundle(
            archive,
            tmp_path / "restore",
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["mrr_at_10", "section_precision_at_5"])
def test_evaluation_rejects_every_nonfinite_floor_metric(field: str, value: float) -> None:
    metrics: dict[str, object] = {
        "mrr_at_10": 1.0,
        "section_precision_at_5": 1.0,
        "queries_run": 5,
    }
    metrics[field] = value
    with pytest.raises(EvaluationRejectedError):
        assert_evaluation_accepted(metrics, expected_queries=5, covered_queries=5)


def _asset_locator(format_name: str, names: list[str], repository: str) -> bytes:
    return json.dumps(
        {
            "format": format_name,
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/{repository}/releases/assets/{index}",
                    "sha256": hashlib.sha256(b"owned").hexdigest(),
                    "size_bytes": len(b"owned"),
                }
                for index, name in enumerate(names, 1)
            ],
        }
    ).encode()


class _SwapResponse:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls = 0

    def __enter__(self) -> _SwapResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"owned"
        self.target.unlink()
        self.target.write_bytes(b"foreign")
        raise OSError("forced failure after substitution")


class _SwapOpener:
    def __init__(self, target: Path) -> None:
        self.target = target

    def open(self, *_args: object, **_kwargs: object) -> _SwapResponse:
        return _SwapResponse(self.target)


def test_rights_cleanup_does_not_delete_substituted_same_name_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "rights"
    destination.mkdir()
    target = destination / "rights-record.json"
    raw = _asset_locator(
        "genereviews-rights-locator-v1",
        ["rights-record.json", "rights-evidence.json", "terms-snapshot.html"],
        "owner/rights",
    )
    monkeypatch.setattr(rights_locator, "build_opener", lambda *_args: _SwapOpener(target))
    token = "".join(("fixture", "-token"))

    with pytest.raises(rights_locator.RightsLocatorError):
        rights_locator.fetch_rights_assets(
            raw,
            allowed_repositories={"owner/rights"},
            destination=destination,
            token=token,
        )
    assert target.read_bytes() == b"foreign"


def test_handoff_cleanup_does_not_delete_substituted_same_name_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_id = "1" * 64
    destination = tmp_path / "handoff"
    destination.mkdir(mode=0o700)
    names = [
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "seal-manifest.json",
        "genereviews_link-5.1.6-py3-none-any.whl",
    ]
    locator = json.loads(_asset_locator("genereviews-handoff-locator-v1", names, "owner/seals"))
    locator.update({"object_id": object_id, "build_revision": "2" * 40})
    target = destination / object_id / "corpus.dump"
    monkeypatch.setattr(handoff_locator, "build_opener", lambda *_args: _SwapOpener(target))
    token = "".join(("fixture", "-token"))

    with pytest.raises(handoff_locator.HandoffLocatorError):
        handoff_locator.fetch_handoff(
            json.dumps(locator).encode(),
            allowed_repositories={"owner/seals"},
            destination_root=destination,
            token=token,
            expected_object_id=object_id,
        )
    assert target.read_bytes() == b"foreign"


@pytest.mark.asyncio
async def test_source_cleanup_does_not_delete_substituted_same_name_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "source"
    destination.mkdir()
    names = sorted(source_locator.SOURCE_ASSETS)
    raw = _asset_locator("genereviews-source-locator-v1", names, "owner/source")
    first = destination / names[0]

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def swapped_stream(_client: object, _url: str, target: Path, **kwargs: object) -> str:
        target.write_bytes(b"owned")
        identity = kwargs.get("created_identity")
        if isinstance(identity, list):
            info = target.stat(follow_symlinks=False)
            identity.append((info.st_dev, info.st_ino))
        target.unlink()
        target.write_bytes(b"foreign")
        return hashlib.sha256(b"owned").hexdigest()

    monkeypatch.setattr(source_locator.httpx, "AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(source_locator, "stream_to_file", swapped_stream)
    token = "".join(("fixture", "-token"))

    with pytest.raises(source_locator.SourceLocatorError):
        await source_locator.fetch_source_assets(
            raw,
            allowed_repositories={"owner/source"},
            destination=destination,
            token=token,
        )
    assert first.read_bytes() == b"foreign"
