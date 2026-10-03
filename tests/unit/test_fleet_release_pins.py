"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert "apt-get upgrade -y" in (ROOT / "docker/Dockerfile").read_text()
    assert (
        "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@0122f6e6d8f6a9057b80134d7cacbf61c5bd2e84"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@0122f6e6d8f6a9057b80134d7cacbf61c5bd2e84"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )
    assert (
        "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
        in (ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text()
    )
    assert (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        in (ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text()
    )


def test_production_compose_uses_the_approved_restart_policy() -> None:
    compose = (ROOT / "docker/docker-compose.prod.yml").read_text()
    assert "restart: unless-stopped" in compose
    assert "restart: on-failure" not in compose
    assert "restart: on-failure" not in (ROOT / "docker/docker-compose.yml").read_text()


def test_container_release_accepts_only_three_component_version_tags() -> None:
    workflow = (ROOT / ".github/workflows/container-release.yml").read_text()
    assert '- "v*.*.*"' in workflow
    assert '- "v*"' not in workflow
