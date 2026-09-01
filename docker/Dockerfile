# Multi-stage Dockerfile for GeneReview-Link

# Exact client binaries for data-only dump/restore compatibility. The same immutable
# PostgreSQL 18/pgvector image is used by corpus build and verification workflows.
FROM pgvector/pgvector:0.8.2-pg18@sha256:42e7f6b4e1eceb02ff14e3e6bc6108bbe259abbe83879dc1845d0da1ddeb555d AS pg18-client

# --- Builder Stage ---
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS builder

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
RUN uv sync --frozen --no-dev --active --extra cpu

# --- Prepared Stage ---
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS prepared

# Keep the PostgreSQL client ABI, binaries, and versioned runtime data together with the
# exact digest-pinned PG18/pgvector build stage.  Production must not resolve a mutable
# package repository or key during image construction.
COPY --from=pg18-client /usr/lib/postgresql/18/bin/pg_dump /usr/local/lib/postgresql/18/bin/pg_dump
COPY --from=pg18-client /usr/lib/postgresql/18/bin/pg_restore /usr/local/lib/postgresql/18/bin/pg_restore
COPY --from=pg18-client /usr/lib/postgresql/18/bin/psql /usr/local/lib/postgresql/18/bin/psql
COPY --from=pg18-client /usr/share/postgresql/18 /usr/share/postgresql/18
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libpq.so.5* /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libsasl2.so.2 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libgnutls.so.30 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libkeyutils.so.1 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libidn2.so.0 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libunistring.so.2 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libtasn1.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libkrb5.so.3 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libk5crypto.so.3 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libcom_err.so.2 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libkrb5support.so.0 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libgssapi_krb5.so.2 /usr/lib/x86_64-linux-gnu/
COPY --from=pg18-client /usr/lib/x86_64-linux-gnu/libp11-kit.so.0 /usr/lib/x86_64-linux-gnu/

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
    PATH="/opt/venv/bin:/usr/local/lib/postgresql/18/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    GENEREVIEW_LINK_HOST=0.0.0.0 \
    GENEREVIEW_LINK_PORT=8000 \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get install -y --only-upgrade --no-install-recommends \
    openssl \
    libssl3t64 \
    openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/genereview-link /var/cache/genereview-link /var/lib/genereview/restore /seed && \
    chown -R app:app /tmp/genereview-link /var/cache/genereview-link /var/lib/genereview /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./genereview_link ./genereview_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/gunicorn_conf.py ./
COPY ./docker/runtime-corpus-files.txt /tmp/runtime-corpus-files.txt
COPY ./docker/runtime-prune-paths.txt /tmp/runtime-prune-paths.txt

# Pin the base image's system pip (/usr/local) to an exact patched release so it
# is not flagged by image scanners (CVE-2025-8869, CVE-2026-1703/3219/6357) and
# the bootstrap stays reproducible, then install the project into the virtualenv.
RUN /usr/local/bin/python -m pip install --no-cache-dir pip==26.1 && \
    /opt/venv/bin/pip install . --no-deps && \
    pg_dump --version | grep -Eq 'PostgreSQL\) 18\.' && \
    pg_restore --version | grep -Eq 'PostgreSQL\) 18\.' && \
    psql --version | grep -Eq 'PostgreSQL\) 18\.'

# Production serving and migration tasks use FakeEmbeddingProvider by default.
# The real BGE embedding stack is used by offline embedding workflows, but its
# PyTorch artifacts exceed the fleet OCI content policy's file/layer limits.
# NOTE: numpy stays because `pgvector.asyncpg` imports it while registering the database
# codec. The full corpus package is offline build/publisher tooling and is not admitted to
# the public image. A small exact subset is retained below for the no-egress direct-restore
# readiness proof; every retained path is reviewed in container-release.json.
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
           /opt/venv/lib/python3.12/site-packages/docutils/writers/odf_odt \
           /opt/venv/lib/python3.12/site-packages/pkg_resources/tests \
           /usr/lib/x86_64-linux-gnu/perl/5.40.1/Data \
           /usr/lib/x86_64-linux-gnu/perl/5.40.1/auto/Data \
           /usr/local/lib/python3.12/ensurepip \
           /usr/share/man \
           /usr/share/postgresql/*/man \
           /root/.cache/pip \
    && find /opt/venv/lib/python3.12/site-packages/numpy -type d -name tests -prune -exec rm -rf '{}' + \
    && rm -rf /home/app/web/genereview_link \
    && package_root="/opt/venv/lib/python3.12/site-packages/genereview_link" \
    && LC_ALL=C sort /tmp/runtime-prune-paths.txt > /tmp/runtime-prune-paths.sorted \
    && cmp -s /tmp/runtime-prune-paths.txt /tmp/runtime-prune-paths.sorted \
    && while IFS= read -r relative; do \
         test -n "$relative" || exit 1; \
         test -e "$package_root/$relative" || exit 1; \
         rm -rf -- "$package_root/$relative"; \
       done < /tmp/runtime-prune-paths.txt \
    && while IFS= read -r relative; do test ! -e "$package_root/$relative"; done \
         < /tmp/runtime-prune-paths.txt \
    && corpus_root="/opt/venv/lib/python3.12/site-packages/genereview_link/corpus" \
    && LC_ALL=C sort /tmp/runtime-corpus-files.txt > /tmp/runtime-corpus-files.sorted \
    && cmp -s /tmp/runtime-corpus-files.txt /tmp/runtime-corpus-files.sorted \
    && while IFS= read -r relative; do test -n "$relative" && test -f "$corpus_root/$relative"; done \
         < /tmp/runtime-corpus-files.txt \
    && find "$corpus_root" -type f -printf '%P\n' | LC_ALL=C sort \
         > /tmp/all-corpus-files.txt \
    && comm -23 /tmp/all-corpus-files.txt /tmp/runtime-corpus-files.txt \
         | while IFS= read -r relative; do rm -f -- "$corpus_root/$relative"; done \
    && find "$corpus_root" -depth -type d -empty -delete \
    && find "$corpus_root" -type f -printf '%P\n' | LC_ALL=C sort \
         > /tmp/admitted-corpus-files.txt \
    && cmp -s /tmp/runtime-corpus-files.txt /tmp/admitted-corpus-files.txt \
    && rm -f /tmp/runtime-corpus-files.txt /tmp/runtime-corpus-files.sorted \
         /tmp/all-corpus-files.txt /tmp/admitted-corpus-files.txt \
         /tmp/runtime-prune-paths.txt /tmp/runtime-prune-paths.sorted \
    && find /usr/local/lib/python3.12 /opt/venv /home/app/web -type d -name __pycache__ -prune -exec rm -rf '{}' +

RUN find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)"

RUN actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 | sort -k2,2)" \
    && { test "$actual" = "$expected" || { printf 'unexpected hardlinks:\n%s\n' "$actual"; exit 1; }; } \
    && rm -f /usr/bin/perl5.40.1

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
    PATH="/opt/venv/bin:/usr/local/lib/postgresql/18/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    GENEREVIEW_LINK_HOST=0.0.0.0 \
    GENEREVIEW_LINK_PORT=8000 \
    TMPDIR=/tmp

WORKDIR /home/app/web
USER app

EXPOSE 8000

# NOTE: intentionally NO image-level HEALTHCHECK (see tests/unit/
# test_dockerfile_healthcheck.py / issue #29): it would apply to the
# `genereview-link embed` one-shot too and report spurious unhealthy. The
# healthcheck is defined per service in the compose files instead.

CMD ["genereview-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
