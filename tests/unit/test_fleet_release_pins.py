"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "apt-get install -y --only-upgrade --no-install-recommends"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@db47bd3357cebf33e6722615c4f0e7419a64857e"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@db47bd3357cebf33e6722615c4f0e7419a64857e"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )
    assert (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
        in (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    )


def test_production_compose_uses_the_approved_restart_policy() -> None:
    compose = (ROOT / "docker/docker-compose.prod.yml").read_text()
    assert "restart: unless-stopped" in compose
    assert "restart: on-failure" not in compose
    assert "restart: on-failure" not in (ROOT / "docker/docker-compose.yml").read_text()
