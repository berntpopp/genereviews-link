"""The workflow client shim always executes exact PG18 tooling."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_pg18_client_shell_executes_digest_pinned_image(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.argv"
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >"$DOCKER_LOG"\n')
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "PG18_MOUNT_ROOT": str(tmp_path),
    }

    subprocess.run(  # noqa: S603 - repository-owned fixed executable
        [root / "scripts/pg18-client", "pg_restore", "--list", str(tmp_path / "x.dump")],
        check=True,
        env=environment,
    )

    arguments = log.read_text().splitlines()
    assert "pgvector/pgvector:0.8.2-pg18@sha256:" in " ".join(arguments)
    assert arguments[-3:] == ["pg_restore", "--list", str(tmp_path / "x.dump")]


def test_pg18_client_shell_rejects_unbounded_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(  # noqa: S603 - repository-owned fixed executable
        [root / "scripts/pg18-client", "psql", "--version"],
        env={**os.environ, "PG18_MOUNT_ROOT": "/"},
        check=False,
    )
    assert result.returncode == 64


def test_pg18_client_forwards_only_required_password_environment(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.argv"
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >"$DOCKER_LOG"\n')
    docker.chmod(0o755)

    subprocess.run(  # noqa: S603 - repository-owned fixed executable
        [root / "scripts/pg18-client", "psql", "--version"],
        check=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "PG18_MOUNT_ROOT": str(tmp_path),
            "PGPASSWORD": "required-secret",
            "UNRELATED_SECRET": "must-not-be-forwarded",
        },
    )

    arguments = log.read_text().splitlines()
    assert [arguments[index + 1] for index, value in enumerate(arguments) if value == "--env"] == [
        "PGPASSWORD"
    ]
    assert "required-secret" not in arguments
    assert "UNRELATED_SECRET" not in arguments
