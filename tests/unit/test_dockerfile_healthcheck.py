"""Regression test: image-level HEALTHCHECK must not return to docker/Dockerfile.

Background (#29): a HEALTHCHECK directive in the image applies to EVERY container
started from that image, including 'genereview-link embed', which does not bind
port 8000. The result is Docker reporting backfill containers as 'unhealthy'
while embeddings are actively being written. Define healthcheck per service in
compose instead.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_has_no_image_level_healthcheck() -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not stripped.upper().startswith("HEALTHCHECK"), (
            "Image-level HEALTHCHECK applies to every command run from the "
            "image, including 'genereview-link embed', and causes spurious "
            "unhealthy status. Define healthcheck per service in compose."
        )
