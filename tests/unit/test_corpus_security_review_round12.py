"""Regressions for strict numeric JSON policy and the verifier's runtime environment.

The rights-path admission findings this round also covered belonged to the fetched
rights-record scheme; that path is gone -- the notice is committed at
``data/RIGHTS.json`` and covered by ``test_rights_notice.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genereview_link.strict_json import StrictJsonError, load_strict_json

ROOT = Path(__file__).resolve().parents[2]


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
