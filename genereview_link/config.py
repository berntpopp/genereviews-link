"""Configuration settings for GeneReview Link.

Manages environment variables and application settings using Pydantic.
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


@dataclass
class ServerConfig:
    """Server configuration with transport selection."""

    transport: Literal["unified", "http", "stdio"] = "unified"
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_path: str = "/mcp"
    enable_docs: bool = True
    log_level: str = "INFO"


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    NCBI_API_KEY: str = ""
    EUTILS_BASE_URL: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # Postgres connection (set in MODE 1/2; empty triggers EutilsClient-only fallback path)
    DATABASE_URL: str = ""
    DATABASE_POOL_MIN_SIZE: int = 2
    DATABASE_POOL_MAX_SIZE: int = 20
    DATABASE_ACQUIRE_TIMEOUT_S: float = 5.0
    # Close idle pool connections after this many seconds.
    DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S: float = 300.0
    # None leaves command timeout behavior to asyncpg/Postgres defaults.
    DATABASE_COMMAND_TIMEOUT_S: float | None = None
    # asyncpg prepared statement cache; use 0 with PgBouncer transaction pooling.
    DATABASE_STATEMENT_CACHE_SIZE: int = 100
    CACHE_SIZE: int = 512
    CACHE_TTL_HOURS: int = 24
    LOG_LEVEL: str = "INFO"
    # Credentialed CORS is disabled at the app layer (unauthenticated backend
    # holds no cookies/session); the startup guard also rejects "*"+credentials.
    # Production origins are injected at runtime via CORS_ORIGINS (comma-separated).
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Distributed rate limiting (for multi-worker deployments)
    RATE_LIMIT_STATE_FILE: str = (
        ""  # Optional: path to shared state file for multi-worker rate
        # limiting
    )

    # Logging configuration
    LOG_JSON: bool = False  # Set to True for JSON logging in production
    ENVIRONMENT: str = "development"  # Environment name for logging context

    # Correlation ID
    CORRELATION_ID_HEADER: str = "X-Request-ID"

    # Metrics
    ENABLE_METRICS: bool = True

    # Transport Configuration (for unified server)
    MCP_TRANSPORT: Literal["unified", "http", "stdio"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"
    MCP_ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", "::1"]
    MCP_ALLOWED_ORIGINS: list[str] = []

    # Ingest parallelism
    INGEST_PARSE_WORKERS: int = 8
    INGEST_DB_WRITERS: int = 4
    INGEST_EMBED_BATCH_SIZE: int = 256
    INGEST_EMBED_WRITERS: int = 2
    INGEST_EMBED_DEVICE: str = "auto"

    # Retrieval / RAG feature flags
    # Set to True to load the BGE-small model at boot (adds ~130MB RAM).
    # When False (default), FakeEmbeddingProvider is used so the server starts
    # quickly in environments without Postgres/GPU resources.
    GENEREVIEW_EAGER_LOAD_BGE: bool = False

    # Set to True to enable the /debug/ranking diagnostic endpoint.
    DEBUG_RANKING_ENABLED: bool = False

    # Corpus bootstrap modes
    # BUNDLE_URL: set to a .tar.gz URL (or "latest") to restore from a release bundle.
    BUNDLE_URL: str = ""
    # EXPECTED_BUNDLE_SHA256: independently-trusted, out-of-band authenticity
    # anchor for the release bundle. Set this from a source OTHER than the
    # (possibly redirected) download host -- e.g. a value reviewed into the
    # deployment config. When set, a downloaded bundle whose SHA-256 does not
    # match is rejected fail-closed. Empty = authenticity unverified (only
    # transport integrity via the sibling .sha256 is checked).
    EXPECTED_BUNDLE_SHA256: str = ""
    # ALLOW_UNANCHORED_BUNDLE: fail-closed guard for the release-bundle restore.
    # A downloaded bundle is promoted only when its authenticity is anchored by
    # an INDEPENDENT committed digest (EXPECTED_BUNDLE_SHA256 or the in-repo
    # BUNDLE_DIGEST_ANCHORS map). The same-host sibling `.sha256` is a transport
    # integrity check ONLY -- a host that can serve a tampered bundle can serve a
    # matching sibling too, so it MUST NOT be the sole authenticity gate. When no
    # anchor is configured, promotion is refused. Set this to True to knowingly
    # accept transport-integrity-only bootstrap (e.g. an air-gapped/dev mirror).
    ALLOW_UNANCHORED_BUNDLE: bool = False
    # Writable directory for bundle download/extraction during bootstrap.
    BUNDLE_BOOTSTRAP_DIR: str = "/tmp/genereview-link"  # noqa: S108
    # BUILD_LOCAL: set to True to run a full local ingest on first boot.
    BUILD_LOCAL: bool = False
    # GITHUB_REPO: owner/repo for release resolution when BUNDLE_URL="latest".
    GITHUB_REPO: str = "berntpopp/genereviews-link"
    # AUTO_PULL_RELEASES: start the hourly release watcher scheduler.
    AUTO_PULL_RELEASES: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("DATABASE_COMMAND_TIMEOUT_S", mode="before")
    @classmethod
    def _normalize_database_command_timeout(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
            return None
        return value

    @field_validator("MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS")
    @classmethod
    def _reject_allowlist_wildcards(cls, values: list[str]) -> list[str]:
        if any(character in entry for entry in values for character in "*?[]"):
            raise ValueError("wildcard entries are not permitted in MCP allowlists")
        return values


settings = Settings()
