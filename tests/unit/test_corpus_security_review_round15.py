"""Regression for the external verifier's exact rights helper call."""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_verifier_imported_rights_helper_accepts_its_sealed_values_call() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    scripts = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["verify"]["steps"])
    match = re.search(r"from ([\w.]+) import verify_rights_record", scripts)
    assert match is not None
    helper = importlib.import_module(match.group(1)).verify_rights_record

    bound = inspect.signature(helper).bind(
        Path("rights-record.json"),
        "0" * 64,
        sealed_values={
            "source_sha256": "1" * 64,
            "artifact_sha256": "2" * 64,
            "corpus_release_id": "2026-09-01-r1",
        },
    )

    assert bound.arguments["sealed_values"]["artifact_sha256"] == "2" * 64
