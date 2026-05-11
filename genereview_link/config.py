"""Configuration settings for GeneReview Link.

Manages environment variables and application settings using Pydantic.
"""

from dataclasses import dataclass
from typing import Literal

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
    DATABASE_POOL_MAX_SIZE: int = 10
    DATABASE_ACQUIRE_TIMEOUT_S: float = 5.0
    CACHE_SIZE: int = 512
    CACHE_TTL_HOURS: int = 24
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"

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

    # Ingest parallelism
    INGEST_PARSE_WORKERS: int = 8
    INGEST_DB_WRITERS: int = 4
    INGEST_EMBED_BATCH_SIZE: int = 256
    INGEST_EMBED_WRITERS: int = 2
    INGEST_EMBED_DEVICE: str = "auto"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
