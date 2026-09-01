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

    # --- Reviewed embedding model artifact ---
    # The model is 127 MiB of weights: too large for the OCI image (the fleet content
    # policy caps a single file at 64 MiB), so it arrives exactly as the corpus does --
    # a digest-pinned release asset, staged read-only on the host, materialised once into
    # a named volume by the no-egress init sidecar, and mounted read-only by the server.
    MODEL_SEED_PATH: str = "/seed/model"
    MODEL_DIR: str = "/var/lib/genereview/models"

    # --- Dense embedding provider ---
    # Which model answers query embeddings. "bge" is the pinned reference model the
    # corpus was embedded with; "fake" is a deterministic stub whose vectors are NOT
    # comparable with the stored corpus vectors, so it disables dense ranking.
    # Empty means: production resolves to "bge" (the safe path is the default where
    # being wrong is expensive), everything else falls back to GENEREVIEW_EAGER_LOAD_BGE.
    # See genereview_link/retrieval/provider_policy.py.
    GENEREVIEW_EMBEDDING_PROVIDER: str = ""
    # Knowingly serve production with the stub provider (lexical-only search). Without
    # this, a stub in production is a startup error rather than a silent ranking
    # regression that still advertises the reference model.
    GENEREVIEW_ALLOW_FAKE_EMBEDDINGS: bool = False
    # LEGACY. Named as a loading strategy but actually selects real-vs-stub embeddings,
    # which is how a production deployment ran on stub vectors unnoticed. Retained for
    # compatibility outside production; GENEREVIEW_EMBEDDING_PROVIDER wins when set.
    GENEREVIEW_EAGER_LOAD_BGE: bool = False

    # Set to True to enable the /debug/ranking diagnostic endpoint.
    DEBUG_RANKING_ENABLED: bool = False

    # --- Immutable corpus artifact (restored-database mode) ---
    # The corpus is an immutable, digest-pinned GitHub data release. It is restored ONCE,
    # by the no-egress `genereview-corpus-restore` init sidecar, into the PostgreSQL
    # volume -- never by the serving application, which has no restore path at all.
    #
    # CORPUS_SEED_PATH: either the reviewed legacy bundle file or a directory containing
    # the exact three direct release assets. The restoring container has no route off the
    # internal network, so it can never fetch them itself.
    CORPUS_SEED_PATH: str = "/seed/corpus-bundle.tar.gz"
    # The currently deployed legacy bundle digest. For a direct release, all three direct
    # asset identities below are required instead. Empty identities fail closed.
    CORPUS_BUNDLE_SHA256: str = ""
    CORPUS_DUMP_SHA256: str = ""
    CORPUS_MANIFEST_SHA256: str = ""
    CORPUS_CHECKSUMS_SHA256: str = ""
    CORPUS_RELEASE_TAG: str = ""
    # CORPUS_RESTORE_DIR: writable scratch (a named volume) for archive expansion.
    CORPUS_RESTORE_DIR: str = "/var/lib/genereview/restore"
    # RESTORE_DATABASE_URL: connection for the RESTORE only, as an unprivileged role that
    # may write the corpus tables and nothing else. Reviewed in-repo migrations run as the
    # owner; the untrusted artifact is loaded with the least rights that can load it.
    RESTORE_DATABASE_URL: str = ""
    # RESTORE_ROLE: the unprivileged role the init ensures (NOSUPERUSER, NOCREATEDB,
    # NOCREATEROLE) before restoring.
    RESTORE_ROLE: str = "genereview_restore"

    # Corpus bootstrap modes
    # BUNDLE_URL: INERT since the no-egress restore sidecar landed (#97, 2026-07-13).
    # The serving process has no restore path, so nothing reads this at request-serving
    # time; a deployment that still sets BUNDLE_URL=latest (production does) is not
    # downloading anything. Retained only because the release-watcher helpers in
    # ingest/github_release.py still resolve release URLs. Tracked for removal in #142.
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
    # RELEASE_WATCHER_ENABLED: run the hourly corpus-staleness watcher. It observes and
    # records into public.genereview_refresh_log; it never pulls.
    RELEASE_WATCHER_ENABLED: bool = False
    # AUTO_PULL_RELEASES: REFUSED. The name promises an automatic corpus pull that was
    # never implemented -- the branch was literally `pass` -- so for months this silently
    # did nothing while reading as "corpus updates are handled". Pulling inside the
    # serving process is also precisely what #97 removed. Setting this true is now a
    # startup error rather than a no-op; use RELEASE_WATCHER_ENABLED for staleness
    # reporting, and the reviewed data-release + init-sidecar path to update the corpus.
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
