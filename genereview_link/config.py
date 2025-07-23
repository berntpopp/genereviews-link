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

    # Transport Configuration (for unified server)
    MCP_TRANSPORT: Literal["unified", "http", "stdio"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
