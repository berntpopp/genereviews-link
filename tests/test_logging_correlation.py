"""Verify that asgi-correlation-id propagates into structlog records."""

from asgi_correlation_id.context import correlation_id


def test_structlog_processor_picks_up_correlation_id() -> None:
    """The custom processor should inject correlation_id from contextvar into the event dict."""
    from genereview_link.logging_config import add_correlation_id

    token = correlation_id.set("abc-123")
    try:
        event = add_correlation_id(None, "info", {"event": "test"})
        assert event["correlation_id"] == "abc-123"
    finally:
        correlation_id.reset(token)


def test_structlog_processor_handles_missing_correlation_id() -> None:
    """When no correlation ID is set, the processor must not crash."""
    from genereview_link.logging_config import add_correlation_id

    event = add_correlation_id(None, "info", {"event": "test"})
    assert "correlation_id" in event
    # Either None or empty string is acceptable as a "not set" sentinel
    assert event["correlation_id"] in (None, "")
