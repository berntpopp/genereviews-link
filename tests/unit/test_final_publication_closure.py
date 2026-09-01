"""Regressions for the final immutable GeneReviews publication closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import genereview_link.corpus.pipeline as pipeline
from genereview_link.corpus.rights import RightsError, verify_rights_record

ROOT = Path(__file__).resolve().parents[2]


def test_release_image_allowlist_contains_current_embedding_identity_migration() -> None:
    release = json.loads((ROOT / "container-release.json").read_bytes())

    assert (
        "opt/venv/lib/python3.12/site-packages/genereview_link/db/migrations/data/"
        "0007_embedding_run_identity.sql"
    ) in release["data"]["image_allowlist"]


@pytest.mark.parametrize(
    "uri_prefix",
    ["", "file:"],
)
def test_rights_record_rejects_nontransferable_filesystem_references(
    tmp_path: Path, uri_prefix: str
) -> None:
    document = tmp_path / "reviewed-rights-document"
    document.write_bytes(b"reviewed terms and evidence\n")
    uri = f"{uri_prefix}{document}"
    object_id = "1" * 64
    record = {
        "artifact_sha256": "2" * 64,
        "object_id": object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": "authority@example.org",
        "decision_time": "2026-08-30T12:00:00Z",
        "terms_uri": uri,
        "terms_sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        "terms_version": "2026-08",
        "permitted_asset_use": "immutable research corpus artifact",
        "attribution": "GeneReviews",
        "evidence_uri": uri,
        "evidence_sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        "source_sha256": "3" * 64,
        "corpus_release_id": "2026-08-30-r1",
    }
    record["rights_record_sha256"] = hashlib.sha256(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    rights = tmp_path / "rights-record.json"
    rights.write_text(json.dumps(record))

    with pytest.raises(RightsError, match="bundle"):
        verify_rights_record(rights, object_id)


@pytest.mark.asyncio
async def test_ingest_records_immutable_identity_before_staging_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fail_record(*args: object, **kwargs: object) -> str:
        del args, kwargs
        calls.append("record")
        raise RuntimeError("record rejected")

    async def prepare(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("staging")

    monkeypatch.setattr(pipeline, "record_corpus_version_start", fail_record)
    monkeypatch.setattr(pipeline, "prepare_staging", prepare)
    monkeypatch.setattr(
        pipeline,
        "load_sidedata",
        lambda _path: SimpleNamespace(short_name_by_nbk={"NBK1": "one"}),
    )
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"retained")

    with pytest.raises(RuntimeError, match="record rejected"):
        await pipeline._ingest_files(
            object(),  # type: ignore[arg-type]
            listing=SimpleNamespace(last_updated="2026-08-31"),
            tarball=archive,
            sidedata_dir=tmp_path,
            tarball_sha256="1" * 64,
            side_data_identity={},
            source_capture={"chapter_ids": ["NBK1"]},
        )

    assert calls == ["record"]


@pytest.mark.asyncio
async def test_mutating_ingest_rejects_live_unretained_sources() -> None:
    with pytest.raises(ValueError, match="retained offline"):
        await pipeline.run_full_ingest(object())  # type: ignore[arg-type]
