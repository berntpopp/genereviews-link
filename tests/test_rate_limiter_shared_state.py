"""Regression tests for distributed rate-limit shared state handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from genereview_link.api import client_manager as client_manager_module
from genereview_link.api.client_manager import DistributedRateLimiter


@pytest.mark.asyncio
async def test_distributed_rate_limiter_creates_parent_and_persists_timestamp(
    tmp_path: Path,
) -> None:
    state = tmp_path / "nested" / "rate-limit.state"
    limiter = DistributedRateLimiter(
        requests_per_second=1000.0,
        shared_state_file=str(state),
    )

    await limiter.wait_if_needed()

    assert limiter.shared_state_file == str(state)
    assert state.exists()
    assert float(state.read_text().strip()) > 0.0


def test_distributed_rate_limiter_probe_does_not_rewrite_live_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "rate-limit.state"
    state.write_text("123.456")
    attempted_writes: list[float] = []

    def fail_on_live_write(self: DistributedRateLimiter, timestamp: float) -> None:
        attempted_writes.append(timestamp)
        raise AssertionError("probe rewrote live shared state")

    monkeypatch.setattr(
        DistributedRateLimiter,
        "_write_shared_state",
        fail_on_live_write,
    )

    limiter = DistributedRateLimiter(
        requests_per_second=1000.0,
        shared_state_file=str(state),
    )

    assert limiter.shared_state_file == str(state)
    assert state.read_text() == "123.456"
    assert attempted_writes == []


@pytest.mark.asyncio
async def test_distributed_rate_limiter_disables_unwritable_state_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "missing-parent" / "rate-limit.state"

    def probe_denied(self: DistributedRateLimiter) -> None:
        raise PermissionError(13, "Permission denied", self.shared_state_file)

    monkeypatch.setattr(
        DistributedRateLimiter,
        "_probe_shared_state_file",
        probe_denied,
        raising=False,
    )
    captured_warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(event: str, **kwargs: object) -> None:
        captured_warnings.append((event, kwargs))

    monkeypatch.setattr(client_manager_module.logger, "warning", capture_warning)

    limiter = DistributedRateLimiter(
        requests_per_second=1000.0,
        shared_state_file=str(state),
    )
    await limiter.wait_if_needed()
    await limiter.wait_if_needed()

    disabled_warnings = [
        warning
        for warning in captured_warnings
        if warning[0] == "Distributed rate limiting shared state disabled"
    ]
    assert len(disabled_warnings) == 1
    assert limiter.shared_state_file is None
    assert limiter._local_last_request > 0.0
    assert all(
        not event.startswith("Failed to write shared state") for event, _ in captured_warnings
    )
