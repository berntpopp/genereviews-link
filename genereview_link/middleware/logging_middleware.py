"""
Logging middleware for request correlation IDs and structured request logging.
"""

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from genereview_link.logging_config import get_logger, log_api_metrics


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request logging with correlation IDs and performance metrics."""

    def __init__(self, app, exclude_paths: list[str] = None):
        super().__init__(app)
        self.logger = get_logger("api.middleware")
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate correlation ID
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Skip logging for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Create request logger with correlation context
        request_logger = self.logger.bind(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
            query_params=str(request.query_params) if request.query_params else None,
            user_agent=request.headers.get("user-agent"),
            client_ip=self._get_client_ip(request),
        )

        start_time = time.time()

        # Log incoming request
        request_logger.info("Incoming request", url=str(request.url))

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Log successful response
            log_api_metrics(
                request_logger,
                status_code=response.status_code,
                duration_ms=duration_ms,
                response_size=response.headers.get("content-length"),
            )

            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id

            return response

        except Exception as e:
            # Calculate duration for failed requests
            duration_ms = (time.time() - start_time) * 1000

            # Log error
            request_logger.error(
                "Request failed",
                duration_ms=round(duration_ms, 2),
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )

            raise

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request headers."""
        # Check for forwarded headers (common in production)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fallback to client host
        if hasattr(request.client, "host"):
            return request.client.host

        return "unknown"
