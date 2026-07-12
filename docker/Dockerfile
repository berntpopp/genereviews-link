# Multi-stage Dockerfile for GeneReview-Link

# --- Builder Stage ---
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY uv.lock pyproject.toml README.md ./

# Use a digest-pinned uv binary so rebuilds are reproducible (no floating
# `pip install uv`). Pin bumps are reviewed as an explicit digest change.
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv
RUN uv sync --frozen --no-dev --active

# --- Production Stage ---
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    GENEREVIEW_LINK_HOST=0.0.0.0 \
    GENEREVIEW_LINK_PORT=8000 \
    TMPDIR=/tmp/genereview-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/genereview-link /var/cache/genereview-link && \
    chown -R app:app /tmp/genereview-link /var/cache/genereview-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./genereview_link ./genereview_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/gunicorn_conf.py ./

# Pin the base image's system pip (/usr/local) to an exact patched release so it
# is not flagged by image scanners (CVE-2025-8869, CVE-2026-1703/3219/6357) and
# the bootstrap stays reproducible, then install the project into the virtualenv.
RUN /usr/local/bin/python -m pip install --no-cache-dir pip==26.1 && \
    /opt/venv/bin/pip install -e . --no-deps

USER app

EXPOSE 8000

# NOTE: intentionally NO image-level HEALTHCHECK (see tests/unit/
# test_dockerfile_healthcheck.py / issue #29): it would apply to the
# `genereview-link embed` one-shot too and report spurious unhealthy. The
# healthcheck is defined per service in the compose files instead.

CMD ["genereview-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
