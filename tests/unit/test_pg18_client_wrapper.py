"""The dockerised PostgreSQL 18 client must forward stdin.

Several call sites in ``.github/workflows/verify-corpus-bundle.yml`` feed SQL to
``psql`` through a heredoc.  ``docker run`` without ``--interactive`` closes stdin, so
psql reads an empty script, prints nothing and still exits 0 -- turning every
``test "$(psql ... <<SQL)" = <value>`` guard into a silent comparison against the empty
string.  That is a failure mode with no diagnostic whatsoever, so it is pinned here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/pg18-client"
WORKFLOW = ROOT / ".github/workflows/verify-corpus-bundle.yml"


def test_client_wrapper_forwards_stdin_to_the_container() -> None:
    script = WRAPPER.read_text(encoding="utf-8")
    arguments = re.search(r"^docker_arguments=\((.*)\)$", script, re.MULTILINE)
    assert arguments is not None, "the wrapper must build its docker arguments in one array"
    assert "--interactive" in arguments.group(1) or " -i " in arguments.group(1)


def test_verifier_proves_stdin_reaches_psql_before_depending_on_it() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["verify"]["steps"]
    scripts = [step.get("run", "") for step in steps]
    guard = next(
        (index for index, run in enumerate(scripts) if "does not receive SQL" in run), None
    )
    assert guard is not None, "the verifier must prove psql stdin round-trips"
    consumers = [
        index
        for index, run in enumerate(scripts)
        if re.search(r"psql[^\n]*\n[^\n]*<<'SQL'", run) or "<<'SQL'" in run
    ]
    assert consumers, "expected at least one heredoc-fed psql call to protect"
    assert guard < min(consumers), "the stdin proof must run before any heredoc-fed psql"
