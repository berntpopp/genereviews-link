"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert "apt-get upgrade -y" in (ROOT / "docker/Dockerfile").read_text()
    assert (
        "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@e8931f0114bba3568517d1c7b1f990bea2be31af"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@e8931f0114bba3568517d1c7b1f990bea2be31af"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )
    assert (
        "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884"
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
