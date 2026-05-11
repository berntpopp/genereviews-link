"""Unit tests for logging utilities in genereview_link.logging_config.

These tests cover the rarely-exercised helpers: PerformanceLogger, LogContext,
log_function_call (both sync and async branches), and small utility functions.
All tests are pure-Python; no network or HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from genereview_link.logging_config import (
    LogContext,
    PerformanceLogger,
    add_log_level,
    add_service_context,
    add_timestamp,
    create_request_logger,
    get_logger,
    json_serializer,
    log_api_metrics,
    log_function_call,
)


class TestPerformanceLogger:
    def test_happy_path_completes_with_info(self) -> None:
        logger = get_logger("test.perf.happy")
        with PerformanceLogger(logger, "op", extra="value") as perf:
            assert isinstance(perf, PerformanceLogger)
            assert perf.start_time is not None
            assert perf.operation == "op"

    def test_add_context_does_not_raise(self) -> None:
        logger = get_logger("test.perf.context")
        with PerformanceLogger(logger, "op") as perf:
            perf.add_context(stage="parsing", count=3)
            # bound logger should be replaced/extended without error
            assert perf.logger is not None

    def test_log_milestone(self) -> None:
        logger = get_logger("test.perf.milestone")
        with PerformanceLogger(logger, "op") as perf:
            perf.log_milestone("step-1", n=1)
            perf.log_milestone("step-2")

    def test_error_path_logs_failure(self) -> None:
        logger = get_logger("test.perf.error")
        with pytest.raises(RuntimeError, match="boom"), PerformanceLogger(logger, "op"):
            raise RuntimeError("boom")


class TestLogContext:
    def test_enter_returns_bound_logger(self) -> None:
        logger = get_logger("test.ctx.bind")
        with LogContext(logger, request_id="abc") as bound:
            assert bound is not None

    def test_exit_logs_exception(self) -> None:
        logger = get_logger("test.ctx.error")
        with pytest.raises(ValueError, match="x"), LogContext(logger, key="v"):
            raise ValueError("x")


class TestLogFunctionCall:
    def test_sync_success(self) -> None:
        logger = get_logger("test.lfc.sync.ok")
        decorator = log_function_call(logger)

        @decorator
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_sync_failure_reraises(self) -> None:
        logger = get_logger("test.lfc.sync.err")
        decorator = log_function_call(logger)

        @decorator
        def boom() -> None:
            raise RuntimeError("no")

        with pytest.raises(RuntimeError, match="no"):
            boom()

    @pytest.mark.asyncio
    async def test_async_success(self) -> None:
        logger = get_logger("test.lfc.async.ok")
        decorator = log_function_call(logger)

        @decorator
        async def add(a: int, b: int) -> int:
            await asyncio.sleep(0)
            return a + b

        result = await add(1, 2)
        assert result == 3

    @pytest.mark.asyncio
    async def test_async_failure_reraises(self) -> None:
        logger = get_logger("test.lfc.async.err")
        decorator = log_function_call(logger)

        @decorator
        async def boom() -> None:
            await asyncio.sleep(0)
            raise ValueError("no async")

        with pytest.raises(ValueError, match="no async"):
            await boom()


class TestProcessors:
    def test_add_timestamp_inserts_ms(self) -> None:
        out = add_timestamp(None, "info", {"event": "x"})
        assert isinstance(out["timestamp"], int)
        assert out["timestamp"] > 0

    def test_add_log_level_uppercases_method_name(self) -> None:
        out = add_log_level(None, "warning", {"event": "x"})
        assert out["level"] == "WARNING"

    def test_add_service_context_injects_known_keys(self) -> None:
        out = add_service_context(None, "info", {})
        assert out["service"] == "genereview-link"
        assert "version" in out
        assert "environment" in out


class TestSerializer:
    def test_json_serializer_returns_bytes_with_newline(self) -> None:
        payload: dict[str, Any] = {"a": 1, "b": "x"}
        data = json_serializer(payload)
        assert isinstance(data, bytes)
        assert data.endswith(b"\n")


class TestRequestUtilities:
    def test_create_request_logger_returns_bound_logger(self) -> None:
        bound = create_request_logger("req-1", "GET", "/health", extra="v")
        # structlog BoundLogger has a bind method; just ensure it's usable.
        assert hasattr(bound, "info")

    def test_log_api_metrics_does_not_raise(self) -> None:
        logger = get_logger("test.api.metrics")
        log_api_metrics(logger, status_code=200, duration_ms=12.3, foo="bar")
