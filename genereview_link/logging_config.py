"""Structured logging configuration using structlog for enhanced observability."""

import logging
import sys
import time
from typing import Any

import orjson
import structlog
from structlog.types import EventDict, Processor

from genereview_link.config import settings


def add_timestamp(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add ISO timestamp to log entries."""
    event_dict["timestamp"] = time.time_ns() // 1_000_000  # milliseconds
    # since epoch
    return event_dict


def add_log_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add log level to event dict."""
    event_dict["level"] = method_name.upper()
    return event_dict


def add_service_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add service context information."""
    event_dict.update(
        {
            "service": "genereview-link",
            "version": "2.0.0",
            "environment": getattr(settings, "ENVIRONMENT", "development"),
        }
    )
    return event_dict


def add_correlation_id(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Inject asgi-correlation-id's request-scoped correlation ID into the log record."""
    from asgi_correlation_id.context import correlation_id

    event_dict["correlation_id"] = correlation_id.get()
    return event_dict


def json_serializer(obj: Any, **kwargs: Any) -> bytes:
    """Fast JSON serializer using orjson."""
    return orjson.dumps(obj, option=orjson.OPT_APPEND_NEWLINE)


def configure_structlog() -> None:
    """Configure structlog for the application."""
    # Determine if we should use JSON logging (production) or console
    # (development)
    use_json = getattr(
        settings,
        "LOG_JSON",
        settings.LOG_LEVEL.upper() in ["INFO", "WARNING", "ERROR"],
    )

    # Common processors for all configurations
    common_processors: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        add_log_level,
        add_timestamp,
        add_service_context,
        add_correlation_id,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    processors: list[Processor]
    if use_json:
        # Production: JSON logging
        processors = [
            *common_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(serializer=json_serializer),
        ]
    else:
        # Development: Console logging with colors
        processors = [
            *common_processors,
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    # Use stderr for MCP compatibility (stdout reserved for JSON protocol)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger for the given name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


# Context managers and utilities for enhanced logging


class LogContext:
    """Context manager for adding structured context to logs."""

    def __init__(self, logger: structlog.stdlib.BoundLogger, **context: Any):
        """Initialize the log context.

        Args:
            logger: The structured logger instance.
            **context: Additional context to bind to the logger.
        """
        self.logger = logger
        self.context = context
        self.bound_logger: structlog.stdlib.BoundLogger | None = None

    def __enter__(self) -> structlog.stdlib.BoundLogger:
        """Enter the context and return bound logger."""
        self.bound_logger = self.logger.bind(**self.context)
        return self.bound_logger

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context and log any exceptions."""
        if exc_type is not None and self.bound_logger is not None:
            self.bound_logger.error(
                "Exception in log context",
                exception_type=exc_type.__name__,
                exception_message=str(exc_val),
            )


def log_function_call(logger: structlog.stdlib.BoundLogger) -> Any:
    """Decorate functions to log function calls with parameters and timing."""

    def decorator(func: Any) -> Any:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            func_logger = logger.bind(function=func.__name__, module=func.__module__)

            func_logger.debug(
                "Function called",
                args_count=len(args),
                kwargs_keys=list(kwargs.keys()),
            )

            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                func_logger.info(
                    "Function completed successfully",
                    duration_ms=round(duration_ms, 2),
                )
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                func_logger.error(
                    "Function failed",
                    duration_ms=round(duration_ms, 2),
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                raise

        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            func_logger = logger.bind(function=func.__name__, module=func.__module__)

            func_logger.debug(
                "Function called",
                args_count=len(args),
                kwargs_keys=list(kwargs.keys()),
            )

            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                func_logger.info(
                    "Function completed successfully",
                    duration_ms=round(duration_ms, 2),
                )
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                func_logger.error(
                    "Function failed",
                    duration_ms=round(duration_ms, 2),
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                raise

        # Return appropriate wrapper based on function type
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Performance logging utilities


class PerformanceLogger:
    """Context manager for performance monitoring."""

    def __init__(
        self,
        logger: structlog.stdlib.BoundLogger,
        operation: str,
        **context: Any,
    ):
        """Initialize the performance logger.

        Args:
            logger: The structured logger instance.
            operation: Name of the operation being monitored.
            **context: Additional context for the operation.
        """
        self.logger = logger.bind(operation=operation, **context)
        self.operation = operation
        self.start_time: float | None = None

    def __enter__(self) -> "PerformanceLogger":
        """Enter the context and start timing."""
        self.start_time = time.time()
        self.logger.debug("Operation started")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context and log performance metrics."""
        duration_ms = (time.time() - (self.start_time or 0)) * 1000

        if exc_type is not None:
            self.logger.error(
                "Operation failed",
                duration_ms=round(duration_ms, 2),
                error_type=exc_type.__name__,
                error_message=str(exc_val),
            )
        else:
            self.logger.info("Operation completed", duration_ms=round(duration_ms, 2))

    def add_context(self, **context: Any) -> None:
        """Add additional context during operation."""
        self.logger = self.logger.bind(**context)

    def log_milestone(self, milestone: str, **context: Any) -> None:
        """Log a milestone during the operation."""
        elapsed_ms = (time.time() - (self.start_time or 0)) * 1000
        self.logger.info(
            "Operation milestone",
            milestone=milestone,
            elapsed_ms=round(elapsed_ms, 2),
            **context,
        )


# HTTP request logging utilities


def create_request_logger(
    request_id: str, method: str, path: str, **context: Any
) -> structlog.stdlib.BoundLogger:
    """Create a logger bound with request context."""
    base_logger = get_logger("api.request")
    return base_logger.bind(request_id=request_id, method=method, path=path, **context)


def log_api_metrics(
    logger: structlog.stdlib.BoundLogger,
    status_code: int,
    duration_ms: float,
    **metrics: Any,
) -> None:
    """Log API request metrics."""
    logger.info(
        "API request completed",
        status_code=status_code,
        duration_ms=round(duration_ms, 2),
        **metrics,
    )
