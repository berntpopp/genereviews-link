"""Regressions for exact-head verifier and finite-value review findings."""

from __future__ import annotations

import hashlib
import io
import math
import tarfile
from pathlib import Path

import pytest
import yaml

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


def test_published_rights_notice_rejects_nonfinite_and_duplicate_json(tmp_path: Path) -> None:
    """The rights notice is metadata a downloader parses, so it gets the same policy."""
    from genereview_link.corpus.rights_notice import RightsNoticeError, load_rights_notice

    for raw in (b'{"schema_version":NaN}', b'{"schema_version":1,"schema_version":2}'):
        notice = tmp_path / "RIGHTS.json"
        notice.write_bytes(raw)
        with pytest.raises(RightsNoticeError, match="not valid JSON"):
            load_rights_notice(notice)


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
