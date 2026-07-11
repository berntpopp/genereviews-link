"""Client lifecycle management for EutilsClient with singleton pattern and distributed rate limiting."""

import asyncio
import os
import tempfile
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any, Optional

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.config import settings
from genereview_link.logging_config import get_logger

logger = get_logger(__name__)


class DistributedRateLimiter:
    """Rate limiter that can work across multiple workers/processes.

    Uses file-based coordination for multi-worker environments.
    """

    def __init__(
        self,
        requests_per_second: float,
        shared_state_file: str | None = None,
    ):
        """Initialize the distributed rate limiter.

        Args:
            requests_per_second: Maximum requests allowed per second.
            shared_state_file: Optional file path for multi-worker coordination.
        """
        self.delay = 1.0 / requests_per_second
        self.shared_state_file = shared_state_file
        self._local_last_request = 0.0
        # asyncio.Lock (NOT threading.Lock): wait_if_needed awaits asyncio.sleep
        # while holding this lock. A threading.Lock held across an await
        # deadlocks the single event loop — a second concurrent caller blocks the
        # loop thread in .acquire() while the holder can never be resumed to
        # release it. asyncio.Lock makes contenders yield cooperatively instead.
        # Cross-process coordination is via the shared state file, not this lock.
        self._lock = asyncio.Lock()
        self._shared_state_warning_emitted = False
        self._prepare_shared_state_file()

    def _prepare_shared_state_file(self) -> None:
        """Create and probe the shared state path once before distributed use."""
        if not self.shared_state_file:
            return
        try:
            directory = os.path.dirname(self.shared_state_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._probe_shared_state_file()
        except OSError as exc:
            self._disable_shared_state(exc)

    def _probe_shared_state_file(self) -> None:
        """Verify the shared state directory is writable without touching live state."""
        if not self.shared_state_file:
            return
        directory = os.path.dirname(self.shared_state_file) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".rate-limit-probe.", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                f.write("probe")
        except OSError:
            with suppress(OSError):
                os.close(fd)
            raise
        finally:
            with suppress(OSError):
                os.unlink(tmp_path)

    def _disable_shared_state(self, exc: OSError) -> None:
        """Disable distributed coordination after logging one visible warning."""
        if not self.shared_state_file:
            return
        state_file = self.shared_state_file
        self.shared_state_file = None
        if self._shared_state_warning_emitted:
            return
        self._shared_state_warning_emitted = True
        logger.warning(
            "Distributed rate limiting shared state disabled",
            state_file=state_file,
            errno=getattr(exc, "errno", None),
            error=str(exc),
        )

    async def _wait_local(self, current_time: float) -> None:
        """Use in-memory timing for single-worker or degraded operation."""
        time_since_last = current_time - self._local_last_request
        if time_since_last < self.delay:
            wait_time = self.delay - time_since_last
            logger.debug(f"Rate limiting: waiting {wait_time:.3f}s")
            await asyncio.sleep(wait_time)
        self._local_last_request = time.time()

    async def wait_if_needed(self) -> None:
        """Wait if necessary to respect rate limits across all workers."""
        async with self._lock:
            current_time = time.time()

            if not self.shared_state_file:
                await self._wait_local(current_time)
                return

            try:
                last_request_time = self._read_shared_state()
                time_since_last = current_time - last_request_time

                if time_since_last < self.delay:
                    wait_time = self.delay - time_since_last
                    logger.debug(f"Rate limiting (distributed): waiting {wait_time:.3f}s")
                    await asyncio.sleep(wait_time)

                self._write_shared_state(time.time())

            except OSError as exc:
                self._disable_shared_state(exc)
                await self._wait_local(current_time)

    def _read_shared_state(self) -> float:
        """Read last request time from shared state file."""
        if not self.shared_state_file:
            return 0.0
        try:
            with open(self.shared_state_file) as f:
                return float(f.read().strip())
        except (FileNotFoundError, ValueError):
            return 0.0

    def _write_shared_state(self, timestamp: float) -> None:
        """Atomically write current timestamp to shared state file."""
        if not self.shared_state_file:
            return
        directory = os.path.dirname(self.shared_state_file) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".rate-limit.", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(str(timestamp))
            os.replace(tmp_path, self.shared_state_file)
        except OSError:
            with suppress(FileNotFoundError):
                os.unlink(tmp_path)
            raise


class ClientManager:
    """Singleton manager for EutilsClient instances with proper lifecycle management."""

    _instance: Optional["ClientManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ClientManager":
        """Create or return the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the ClientManager instance."""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._client: EutilsClient | None = None
        self._client_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()

        # Setup distributed rate limiting
        shared_state_file = getattr(settings, "RATE_LIMIT_STATE_FILE", None)
        if shared_state_file:
            logger.info("Using distributed rate limiting", state_file=shared_state_file)

        # Calculate rate limit based on API key
        requests_per_second = 10.0 if settings.NCBI_API_KEY else 3.0
        self._rate_limiter = DistributedRateLimiter(
            requests_per_second=requests_per_second,
            shared_state_file=shared_state_file,
        )

        logger.info(
            "ClientManager initialized",
            rate_limit_rps=requests_per_second,
            has_api_key=bool(settings.NCBI_API_KEY),
            shared_state_file=shared_state_file or "none",
        )

    async def get_client(self) -> EutilsClient:
        """Get or create the singleton EutilsClient instance."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    logger.info("Creating new EutilsClient instance")
                    # Create client with rate limiter injection
                    self._client = EutilsClient()
                    # Replace the client's rate limiting with our distributed version.
                    # EutilsClient discovers these via ``hasattr`` at call time, so they
                    # are duck-typed extensions rather than declared fields.
                    self._client._rate_limiter = self._rate_limiter  # type: ignore[attr-defined]
                    self._client._distributed_wait = (  # type: ignore[attr-defined]
                        self._rate_limiter.wait_if_needed
                    )

        return self._client

    @asynccontextmanager
    async def get_client_context(self) -> AsyncGenerator[EutilsClient, None]:
        """Context manager for getting client (for dependency injection)."""
        client = await self.get_client()
        try:
            yield client
        finally:
            # Don't close here - let the manager handle lifecycle
            pass

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        async with self._client_lock:
            if self._client is not None:
                logger.info("Closing EutilsClient instance")
                await self._client.close()
                self._client = None

        self._shutdown_event.set()

    async def health_check(self, test_connection: bool = False) -> dict[str, Any]:
        """Check the health of the client connection."""
        try:
            client = await self.get_client()

            base_health = {
                "status": "ready",
                "client_id": id(client.client),
                "rate_limit_delay": client.rate_limit_delay,
                "has_api_key": bool(settings.NCBI_API_KEY),
                "base_url": client.base_url,
            }

            # Only test actual connection if requested
            if test_connection:
                start_time = time.time()

                # Use a simple einfo request as health check with shorter timeout
                response = await client.client.get(
                    f"{client.base_url}/einfo.fcgi",
                    params={"retmode": "json"},
                    timeout=5.0,  # Short timeout for health checks
                )
                response.raise_for_status()

                response_time = time.time() - start_time
                base_health.update(
                    {
                        "status": "healthy",
                        "response_time_ms": round(response_time * 1000, 2),
                        "connection_tested": True,
                    }
                )

            return base_health

        except Exception as e:
            # SECURITY: /health returns this under HTTP 200; never surface or log
            # str(e) (it can carry control/zero-width/bidi/NUL or upstream detail).
            # Keep the ``error`` field for schema stability with FIXED text only.
            logger.warning("Health check failed", error_type=type(e).__name__)
            return {
                "status": "degraded",
                "error": "Upstream health check failed.",
                "connection_tested": test_connection,
            }


# Global instance (lazily initialized)
_client_manager: ClientManager | None = None


async def get_managed_client() -> AsyncGenerator[EutilsClient, None]:
    """Get managed client dependency for FastAPI."""
    client_manager = await get_client_manager()
    async with client_manager.get_client_context() as client:
        yield client


async def get_client_manager() -> ClientManager:
    """Get the global client manager instance."""
    global _client_manager
    if _client_manager is None:
        _client_manager = ClientManager()
    return _client_manager


async def shutdown_clients() -> None:
    """Shutdown all managed clients (call from app shutdown)."""
    if _client_manager is not None:
        await _client_manager.close()
