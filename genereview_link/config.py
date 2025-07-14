"""Configuration settings for GeneReview Link.

Manages environment variables and application settings using Pydantic.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
