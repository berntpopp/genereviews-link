"""Keep the public image on PyTorch's explicit CPU wheel index."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_embedding_accelerators_are_conflicting_explicit_extras() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    dependencies = project["project"]["dependencies"]
    # `tokenizers` is deliberately NOT in this list: the serving image runs the pinned BGE
    # weights through ONNX Runtime and needs the tokenizer. It is the PyTorch stack that
    # must stay out -- torch's wheel alone is 526 MB, over the fleet content policy's
    # 64 MiB per-file ceiling.
    assert all(
        not str(item).startswith(("sentence-transformers", "torch", "transformers"))
        for item in dependencies
    )
    assert any(str(item).startswith("onnxruntime") for item in dependencies)
    assert any(str(item).startswith("tokenizers") for item in dependencies)
    extras = project["project"]["optional-dependencies"]
    assert any(str(item).startswith("torch>=2.13.0") for item in extras["cpu"])
    assert any(str(item).startswith("torch>=2.13.0") for item in extras["cu130"])
    uv = project["tool"]["uv"]
    assert [{"extra": "cpu"}, {"extra": "cu130"}] in uv["conflicts"]
    assert uv["sources"]["torch"] == [
        {"index": "pytorch-cpu", "extra": "cpu"},
        {"index": "pytorch-cu130", "extra": "cu130"},
    ]


def test_cpu_export_excludes_cuda_and_docker_selects_cpu() -> None:
    uv = shutil.which("uv")
    assert uv is not None
    exported = subprocess.run(  # noqa: S603 -- fixed executable with a literal argv
        [uv, "export", "--frozen", "--no-dev", "--extra", "cpu", "--no-emit-project"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.lower()
    assert "torch==2.13.0+cpu" in exported
    assert not any(name in exported for name in ("nvidia-", "cuda-", "triton=="))
    assert "uv sync --frozen --no-dev --active --extra cpu" in (
        ROOT / "docker" / "Dockerfile"
    ).read_text(encoding="utf-8")
