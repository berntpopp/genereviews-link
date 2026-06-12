# Multi-stage Dockerfile for GeneReview-Link

# --- Builder Stage ---
FROM python:3.12-slim AS builder

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

# Upgrade pip to a patched release (clears CVE-2025-8869, CVE-2026-1703,
# CVE-2026-3219, CVE-2026-6357 flagged by image scanners) before installing uv.
RUN pip install --upgrade "pip>=26.1" uv && \
    uv sync --frozen --no-dev --active

# --- Production Stage ---
FROM python:3.12-slim AS production

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

# Upgrade the base image's system pip (/usr/local) to a patched release so it is
# not flagged by image scanners (CVE-2025-8869, CVE-2026-1703/3219/6357), then
# install the project into the (already patched) virtualenv.
RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade "pip>=26.1" && \
    /opt/venv/bin/pip install -e . --no-deps

USER app

EXPOSE 8000

CMD ["genereview-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
