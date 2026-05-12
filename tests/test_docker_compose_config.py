from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: _ComposeLoader, node: yaml.Node) -> Any:
    return loader.construct_sequence(node)


_ComposeLoader.add_constructor("!reset", _construct_reset)


def _compose_config(*files: str) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")

    cmd = ["docker", "compose"]
    for file in files:
        cmd.extend(["-f", str(REPO_ROOT / file)])
    cmd.extend(["--env-file", str(REPO_ROOT / ".env.docker.example"), "config"])

    result = subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    loaded = yaml.safe_load(result.stdout)
    assert isinstance(loaded, dict)
    return loaded


def test_npm_overlay_keeps_app_connected_to_postgres_network() -> None:
    config = _compose_config(
        "docker/docker-compose.yml",
        "docker/docker-compose.prod.yml",
        "docker/docker-compose.npm.yml",
    )

    services = config["services"]
    app_networks = services["genereview-link"]["networks"]
    postgres_networks = services["postgres"]["networks"]

    assert "default" in app_networks
    assert "npm-network" in app_networks
    assert "default" in postgres_networks


def test_docker_env_file_is_injected_into_app_container() -> None:
    compose = yaml.load(
        (REPO_ROOT / "docker/docker-compose.yml").read_text(),
        Loader=_ComposeLoader,  # noqa: S506
    )

    env_files = compose["services"]["genereview-link"].get("env_file", [])
    paths = {entry["path"] for entry in env_files}
    assert "../.env" in paths
    assert "../.env.docker" in paths


def test_production_compose_uses_unified_cli_server_not_gunicorn_env() -> None:
    config = _compose_config("docker/docker-compose.yml", "docker/docker-compose.prod.yml")

    service = config["services"]["genereview-link"]
    assert service["command"] == [
        "genereview-link",
        "serve",
        "--transport",
        "unified",
        "--host",
        "0.0.0.0",  # noqa: S104
        "--port",
        "8000",
    ]
    assert "GUNICORN_WORKERS" not in service["environment"]
    assert "GUNICORN_LOG_LEVEL" not in service["environment"]
