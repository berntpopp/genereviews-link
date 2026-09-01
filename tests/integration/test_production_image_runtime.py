"""Runtime contract for the exact production OCI target."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

IMAGE = os.getenv("GENEREVIEW_PRODUCTION_IMAGE")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not IMAGE, reason="set GENEREVIEW_PRODUCTION_IMAGE to test the built image"),
]


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    assert IMAGE is not None
    docker = shutil.which("docker")
    assert docker is not None
    return subprocess.run(  # noqa: S603 - explicit Docker argv tests the local image
        [docker, "run", "--rm", "--entrypoint", arguments[0], IMAGE, *arguments[1:]],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_production_image_imports_installed_application_and_corpus() -> None:
    result = _run(
        "python",
        "-I",
        "-c",
        (
            "import pathlib,genereview_link,genereview_link.corpus,genereview_link.db.restore;"
            "import genereview_link.corpus.readiness;"
            "root=pathlib.Path('/opt/venv/lib/python3.12/site-packages').resolve();"
            "paths=[pathlib.Path(x.__file__).resolve() for x in "
            "(genereview_link,genereview_link.corpus,genereview_link.corpus.readiness,"
            "genereview_link.db.restore)];"
            "migration=root/'genereview_link/db/migrations/control/0007_release_readiness.sql';"
            "assert migration.is_file(),migration;"
            "assert all(root in path.parents for path in paths),paths;print(*paths,sep='\\n')"
        ),
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tool", ["pg_dump", "pg_restore", "psql"])
def test_production_image_carries_exact_postgresql_18_clients(tool: str) -> None:
    result = _run(tool, "--version")

    assert result.returncode == 0, result.stderr
    assert "PostgreSQL) 18." in result.stdout
