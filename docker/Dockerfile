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

# --- Prepared Stage ---
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS prepared


# Release identity is supplied by the central container-release workflow.
ARG APP_VERSION=0.0.0
ARG VCS_REF=0000000000000000000000000000000000000000
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="GeneReviews Link" \
      org.opencontainers.image.description="FastAPI and MCP server for NCBI GeneReviews research data" \
      org.opencontainers.image.source="https://github.com/berntpopp/genereviews-link" \
      org.opencontainers.image.url="https://github.com/berntpopp/genereviews-link" \
      org.opencontainers.image.documentation="https://github.com/berntpopp/genereviews-link#readme" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry"
ENV VCS_REF="${VCS_REF}"

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
    /opt/venv/bin/pip install . --no-deps

# Production serving and migration tasks use FakeEmbeddingProvider by default.
# The real BGE embedding stack is used by offline embedding workflows, but its
# PyTorch artifacts exceed the fleet OCI content policy's file/layer limits.
RUN rm -rf /opt/venv/lib/python3.12/site-packages/nvidia \
           /opt/venv/lib/python3.12/site-packages/nvidia_*.dist-info \
           /opt/venv/lib/python3.12/site-packages/triton \
           /opt/venv/lib/python3.12/site-packages/triton-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/cuda \
           /opt/venv/lib/python3.12/site-packages/cuda_*.dist-info \
           /opt/venv/lib/python3.12/site-packages/functorch \
           /opt/venv/lib/python3.12/site-packages/hf_xet \
           /opt/venv/lib/python3.12/site-packages/hf_xet-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/huggingface_hub \
           /opt/venv/lib/python3.12/site-packages/huggingface_hub-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/joblib \
           /opt/venv/lib/python3.12/site-packages/joblib-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/mpmath \
           /opt/venv/lib/python3.12/site-packages/mpmath-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/networkx \
           /opt/venv/lib/python3.12/site-packages/networkx-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/numpy \
           /opt/venv/lib/python3.12/site-packages/numpy-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/numpy.libs \
           /opt/venv/lib/python3.12/site-packages/safetensors \
           /opt/venv/lib/python3.12/site-packages/safetensors-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/scipy \
           /opt/venv/lib/python3.12/site-packages/scipy-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/scipy.libs \
           /opt/venv/lib/python3.12/site-packages/sentence_transformers \
           /opt/venv/lib/python3.12/site-packages/sentence_transformers-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/sklearn \
           /opt/venv/lib/python3.12/site-packages/scikit_learn-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/sympy \
           /opt/venv/lib/python3.12/site-packages/sympy-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/threadpoolctl.py \
           /opt/venv/lib/python3.12/site-packages/threadpoolctl-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/tokenizers \
           /opt/venv/lib/python3.12/site-packages/tokenizers-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/torch \
           /opt/venv/lib/python3.12/site-packages/torch-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/torchgen \
           /opt/venv/lib/python3.12/site-packages/transformers \
           /opt/venv/lib/python3.12/site-packages/transformers-*.dist-info \
           /opt/venv/lib/python3.12/site-packages/genereview_link/corpus \
           /opt/venv/lib/python3.12/site-packages/docutils/writers/odf_odt \
           /opt/venv/lib/python3.12/site-packages/pkg_resources/tests \
           /usr/lib/x86_64-linux-gnu/perl/5.40.1/Data \
           /usr/lib/x86_64-linux-gnu/perl/5.40.1/auto/Data \
           /usr/local/lib/python3.12/ensurepip \
           /usr/share/man \
           /usr/share/postgresql/*/man \
           /root/.cache/pip \
           /home/app/web/genereview_link \
    && find /usr/local/lib/python3.12 /opt/venv /home/app/web -type d -name __pycache__ -prune -exec rm -rf '{}' +

RUN find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)"

RUN actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 \
         "$(stat -c '%i' /usr/bin/perlbug)" /usr/bin/perlbug \
         "$(stat -c '%i' /usr/bin/perlthanks)" /usr/bin/perlthanks | sort -k2,2)" \
    && { test "$actual" = "$expected" || { printf 'unexpected hardlinks:\n%s\n' "$actual"; exit 1; }; } \
    && rm -f /usr/bin/perl5.40.1 /usr/bin/perlthanks

# --- Production Stage ---
FROM scratch AS production
COPY --from=prepared / /

# Release identity is supplied by the central container-release workflow.
ARG APP_VERSION=0.0.0
ARG VCS_REF=0000000000000000000000000000000000000000
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="GeneReviews Link" \
      org.opencontainers.image.description="FastAPI and MCP server for NCBI GeneReviews research data" \
      org.opencontainers.image.source="https://github.com/berntpopp/genereviews-link" \
      org.opencontainers.image.url="https://github.com/berntpopp/genereviews-link" \
      org.opencontainers.image.documentation="https://github.com/berntpopp/genereviews-link#readme" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry"
ENV VCS_REF="${VCS_REF}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    GENEREVIEW_LINK_HOST=0.0.0.0 \
    GENEREVIEW_LINK_PORT=8000 \
    TMPDIR=/tmp/genereview-link

WORKDIR /home/app/web
USER app

EXPOSE 8000

# NOTE: intentionally NO image-level HEALTHCHECK (see tests/unit/
# test_dockerfile_healthcheck.py / issue #29): it would apply to the
# `genereview-link embed` one-shot too and report spurious unhealthy. The
# healthcheck is defined per service in the compose files instead.

CMD ["genereview-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
