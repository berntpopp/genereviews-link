from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    NCBI_API_KEY: str = ""
    EUTILS_BASE_URL: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    CACHE_SIZE: int = 512
    CACHE_TTL_HOURS: int = 24
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()