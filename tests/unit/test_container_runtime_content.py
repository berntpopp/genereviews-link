"""Production image content stays inside the reviewed runtime boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CORPUS_FILES = (
    "__init__.py",
    "computation_runs.py",
    "evaluation-suite.txt",
    "evaluation.py",
    "evaluation_contract.py",
    "freshness.py",
    "jsonb.py",
    "readiness.py",
    "semantic_identity.py",
)
INSTALLED_CORPUS_PREFIX = "opt/venv/lib/python3.12/site-packages/genereview_link/corpus/"
PROHIBITED_RUNTIME_PATHS = (
    "download_admission.py",
    "download_guard.py",
    "ingest",
    "publisher_verifier",
)


def _runtime_manifest() -> tuple[str, ...]:
    manifest = ROOT / "docker/runtime-corpus-files.txt"
    return tuple(manifest.read_text(encoding="utf-8").splitlines())


def _runtime_prune_paths() -> tuple[str, ...]:
    manifest = ROOT / "docker/runtime-prune-paths.txt"
    return tuple(manifest.read_text(encoding="utf-8").splitlines())


def test_runtime_corpus_manifest_is_the_exact_restore_readiness_closure() -> None:
    """The public image must not admit offline build or publisher modules."""
    assert _runtime_manifest() == RUNTIME_CORPUS_FILES

    release = json.loads((ROOT / "container-release.json").read_bytes())
    corpus_allowlist = {
        path for path in release["data"]["image_allowlist"] if "/genereview_link/corpus/" in path
    }
    assert corpus_allowlist == {
        f"{INSTALLED_CORPUS_PREFIX}{relative}" for relative in RUNTIME_CORPUS_FILES
    }


def test_dockerfile_prunes_source_duplicate_and_offline_corpus_modules() -> None:
    dockerfile = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8")

    assert "COPY ./docker/runtime-corpus-files.txt /tmp/runtime-corpus-files.txt" in dockerfile
    assert 'find "$corpus_root" -type f -printf' in dockerfile
    assert "comm -23 /tmp/all-corpus-files.txt" in dockerfile
    assert "rm -rf /home/app/web/genereview_link" in dockerfile


def test_dockerfile_prunes_the_exact_offline_package_paths() -> None:
    assert _runtime_prune_paths() == PROHIBITED_RUNTIME_PATHS
    dockerfile = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8")

    assert "COPY ./docker/runtime-prune-paths.txt /tmp/runtime-prune-paths.txt" in dockerfile
    assert 'rm -rf -- "$package_root/$relative"' in dockerfile
    assert 'test ! -e "$package_root/$relative"' in dockerfile


def test_direct_restore_uses_the_retained_runtime_index_module() -> None:
    cli = (ROOT / "genereview_link/cli.py").read_text(encoding="utf-8")
    restore = cli.split('@corpus_app.command("restore")', maxsplit=1)[1]

    assert "from genereview_link.db.indexes import build_hnsw_index" in restore
    assert "from genereview_link.ingest.orchestrator import build_hnsw_index" not in restore


def test_pruned_runtime_package_imports_cli_and_direct_restore_readiness(tmp_path: Path) -> None:
    """Exercise the imports used by the image CMD and no-egress restore sidecar."""
    package = tmp_path / "genereview_link"
    shutil.copytree(
        ROOT / "genereview_link",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    corpus = package / "corpus"
    retained = set(_runtime_manifest())
    for candidate in corpus.rglob("*"):
        if candidate.is_file() and candidate.relative_to(corpus).as_posix() not in retained:
            candidate.unlink()
    for candidate in sorted(corpus.rglob("*"), reverse=True):
        if candidate.is_dir() and not any(candidate.iterdir()):
            candidate.rmdir()
    for relative in _runtime_prune_paths():
        candidate = package / relative
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()
    assert all(not (package / relative).exists() for relative in PROHIBITED_RUNTIME_PATHS)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "import genereview_link.cli as cli; "
                "import genereview_link.corpus.readiness as readiness; "
                "import genereview_link.db.indexes as indexes; "
                "assert Path(cli.__file__).is_relative_to(Path.cwd()); "
                "assert readiness.READINESS_KEYS; "
                "assert indexes.build_hnsw_index"
            ),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
