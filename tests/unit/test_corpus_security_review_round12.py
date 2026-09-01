"""Regressions for verifier environment and rights-path admission findings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from genereview_link.corpus import rights_locator
from genereview_link.strict_json import StrictJsonError, load_strict_json

ROOT = Path(__file__).resolve().parents[2]
OWNED = b"owned"


@pytest.mark.parametrize(
    "token",
    (b"9" * 5_000, b"1e999", b"1." + b"0" * 129),
    ids=("oversized-integer", "infinite-float", "oversized-float"),
)
def test_shared_strict_json_enforces_explicit_numeric_policy(token: bytes) -> None:
    with pytest.raises(StrictJsonError, match="numeric token"):
        load_strict_json(b'{"value":' + token + b"}", max_bytes=8_192)


def test_verifier_runs_repository_cli_through_uv_environment() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    steps = workflow["jobs"]["verify"]["steps"]
    migrate = next(step for step in steps if step.get("name", "").startswith("Apply reviewed"))
    rebuild = next(step for step in steps if step.get("name", "").startswith("Rebuild HNSW"))

    assert migrate["run"] == "uv run genereview-link db migrate"
    assert rebuild["run"] == "uv run genereview-link embed --index-only"


def test_build_selection_never_uses_source_only_published_noop() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/corpus-data-release.yml").read_text())
    steps = workflow["jobs"]["build"]["steps"]
    selection = next(
        step["run"] for step in steps if step.get("name", "").startswith("Select identity-aware")
    )

    assert "published_noop source identity candidate" not in selection
    assert "source-only builds have no secret" not in selection
    assert 'release_id="$first_free_release_id"' in selection
    assert "Full eight-asset no-op" in selection
    assert "only by plan_release" in selection


def _locator() -> bytes:
    assets = [
        {
            "name": name,
            "url": f"https://api.github.com/repos/owner/rights/releases/assets/{index}",
            "sha256": hashlib.sha256(OWNED).hexdigest(),
            "size_bytes": len(OWNED),
        }
        for index, name in enumerate(
            ("rights-record.json", "rights-evidence.json", "terms-snapshot.html"), 1
        )
    ]
    return json.dumps(
        {"format": "genereviews-rights-locator-v1", "assets": assets},
        separators=(",", ":"),
    ).encode()


class _SwapAtEofResponse:
    def __init__(self, target: Path | None) -> None:
        self.target = target
        self.calls = 0

    def __enter__(self) -> _SwapAtEofResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return OWNED
        if self.target is not None:
            self.target.unlink()
            self.target.write_bytes(b"foreign")
            self.target = None
        return b""


class _SwapFirstOpener:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls = 0

    def open(self, *_args: object, **_kwargs: object) -> _SwapAtEofResponse:
        self.calls += 1
        return _SwapAtEofResponse(self.target if self.calls == 1 else None)


def test_rights_fetch_rejects_path_substitution_after_owned_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "rights"
    destination.mkdir()
    target = destination / "rights-record.json"
    monkeypatch.setattr(
        rights_locator,
        "build_opener",
        lambda *_args: _SwapFirstOpener(target),
    )

    with pytest.raises(rights_locator.RightsLocatorError, match="admitted identity"):
        rights_locator.fetch_rights_assets(
            _locator(),
            allowed_repositories={"owner/rights"},
            destination=destination,
            token="fixture-token",  # noqa: S106 - non-secret fixture
        )

    assert target.read_bytes() == b"foreign"
