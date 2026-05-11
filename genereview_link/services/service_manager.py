"""Service lifecycle management with singleton pattern and proper client integration."""

import asyncio
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Optional

from genereview_link.api.client_manager import get_client_manager
from genereview_link.logging_config import get_logger
from genereview_link.services.genereview_service import GeneReviewService

logger = get_logger(__name__)


class ServiceManager:
    """Singleton manager for GeneReviewService instances with proper lifecycle management."""

    _instance: Optional["ServiceManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ServiceManager":
        """Create or return the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the ServiceManager instance."""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._service: GeneReviewService | None = None
        self._service_lock = asyncio.Lock()

        logger.info("ServiceManager initialized")

    async def get_service(self) -> GeneReviewService:
        """Get or create the singleton GeneReviewService instance."""
        if self._service is None:
            async with self._service_lock:
                if self._service is None:
                    logger.info("Creating new GeneReviewService instance")
                    # Get managed client from client manager
                    client_manager = await get_client_manager()
                    client = await client_manager.get_client()
                    self._service = GeneReviewService(client=client)

        return self._service

    @asynccontextmanager
    async def get_service_context(self) -> AsyncGenerator[GeneReviewService, None]:
        """Context manager for getting service (for dependency injection)."""
        service = await self.get_service()
        try:
            yield service
        finally:
            # Don't close here - let the manager handle lifecycle
            pass

    async def close(self) -> None:
        """Close the service and cleanup resources."""
        async with self._service_lock:
            if self._service is not None:
                logger.info("Closing GeneReviewService instance")
                await self._service.close()
                self._service = None


# Global instance (lazily initialized)
_service_manager: ServiceManager | None = None


async def get_managed_service() -> AsyncGenerator[GeneReviewService, None]:
    """Get managed service dependency for FastAPI."""
    service_manager = await get_service_manager()
    async with service_manager.get_service_context() as service:
        yield service


async def get_service_manager() -> ServiceManager:
    """Get the global service manager instance."""
    global _service_manager
    if _service_manager is None:
        _service_manager = ServiceManager()
    return _service_manager


async def shutdown_services() -> None:
    """Shutdown all managed services (call from app shutdown)."""
    if _service_manager is not None:
        await _service_manager.close()
