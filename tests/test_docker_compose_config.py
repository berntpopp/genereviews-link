from __future__ import annotations

import json
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
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_ComposeLoader.add_constructor("!reset", _construct_reset)
_ComposeLoader.add_constructor("!override", _construct_reset)


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


def test_compose_sets_explicit_project_name() -> None:
    compose = yaml.load(
        (REPO_ROOT / "docker/docker-compose.yml").read_text(),
        Loader=_ComposeLoader,  # noqa: S506
    )

    assert compose["name"] == "genereviews-link"


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
    # PostgreSQL sits on the internal-only network -- it has no route off the host -- so the
    # app must stay attached to it as well or it cannot reach its own database.
    assert "genereview_internal" in app_networks
    assert set(postgres_networks) == {"genereview_internal"}


def test_docker_env_file_is_injected_into_app_container() -> None:
    compose = yaml.load(
        (REPO_ROOT / "docker/docker-compose.yml").read_text(),
        Loader=_ComposeLoader,  # noqa: S506
    )

    env_files = compose["services"]["genereview-link"].get("env_file", [])
    paths = {entry["path"] for entry in env_files}
    assert "../.env" in paths
    assert "../.env.docker" in paths


def test_postgres_volume_mount_matches_pg18_data_layout() -> None:
    compose = yaml.load(
        (REPO_ROOT / "docker/docker-compose.yml").read_text(),
        Loader=_ComposeLoader,  # noqa: S506
    )

    volumes = compose["services"]["postgres"]["volumes"]
    targets = {mount["target"]: mount for mount in volumes}
    # The image's own /var/lib/postgresql is postgres-owned and 1777, so an empty named
    # volume mounted THERE inherits that ownership and uid 999 can create PGDATA itself --
    # no chown, hence no CAP_CHOWN, hence `cap_drop: ALL` still holds. Mounting one level
    # deeper (/var/lib/postgresql/data) yields a root-owned volume and the container dies.
    assert targets["/var/lib/postgresql"]["source"] == "genereview_pg_data"
    assert "/var/lib/postgresql/data" not in targets
    # The unix socket dir is a NAMED VOLUME, not a tmpfs: the central smoke stack replaces a
    # sidecar's tmpfs list wholesale, so a tmpfs socket dir vanishes there.
    assert targets["/var/run/postgresql"]["source"] == "genereview_pg_run"


def test_docker_example_pins_the_reviewed_corpus_artifact() -> None:
    """The server no longer resolves `latest`: it never downloads a corpus at all."""
    env = {}
    for raw_line in (REPO_ROOT / ".env.docker.example").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        env[key] = value

    assert "BUNDLE_URL" not in env
    assert env["CORPUS_SEED_DIR"]
    declared = json.loads((REPO_ROOT / "container-release.json").read_text())
    assert env["CORPUS_BUNDLE_SHA256"] == declared["data"]["digest"].removeprefix("sha256:")


def test_production_compose_uses_unified_cli_server_not_gunicorn_env() -> None:
    config = _compose_config("docker/docker-compose.yml", "docker/docker-compose.prod.yml")

    service = config["services"]["genereview-link"]
    # The central policy forbids overriding the image process, so the unified server is the
    # image CMD rather than a compose `command:`. Assert it there instead.
    assert "command" not in service
    dockerfile = (REPO_ROOT / "docker/Dockerfile").read_text()
    assert (
        'CMD ["genereview-link", "serve", "--transport", "unified", '
        '"--host", "0.0.0.0", "--port", "8000"]' in dockerfile
    )
    assert "GUNICORN_WORKERS" not in service["environment"]
    assert "GUNICORN_LOG_LEVEL" not in service["environment"]


def test_production_tmpfs_is_the_writable_scratch_the_image_points_tmpdir_at() -> None:
    """The approved writable scratch path is exactly /tmp, and TMPDIR must resolve inside it.

    A tmpfs at /tmp HIDES any directory the image created under it, so a TMPDIR of
    /tmp/<something> would point at a path that does not exist at runtime.
    """
    compose = yaml.load(
        (REPO_ROOT / "docker/docker-compose.prod.yml").read_text(),
        Loader=_ComposeLoader,  # noqa: S506
    )

    tmpfs = compose["services"]["genereview-link"]["tmpfs"]

    assert "/tmp:rw,noexec,nosuid,size=512m,mode=1777" in tmpfs  # noqa: S108
    dockerfile = (REPO_ROOT / "docker/Dockerfile").read_text()
    assert "TMPDIR=/tmp\n" in dockerfile or "TMPDIR=/tmp " in dockerfile


def test_npm_overlay_inherits_production_tmpfs_mode() -> None:
    config = _compose_config(
        "docker/docker-compose.yml",
        "docker/docker-compose.prod.yml",
        "docker/docker-compose.npm.yml",
    )

    tmpfs = config["services"]["genereview-link"]["tmpfs"]

    assert any(
        entry.startswith("/tmp:")  # noqa: S108
        and "size=512m" in entry
        and "mode=1777" in entry
        for entry in tmpfs
    )
