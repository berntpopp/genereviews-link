"""Regression tests for distributed rate-limit shared state handling."""

from __future__ import annotations

import asyncio
import threading
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


def test_concurrent_callers_do_not_deadlock_event_loop(tmp_path: Path) -> None:
    """Concurrent wait_if_needed callers must not wedge the single event loop.

    Regression: wait_if_needed awaits asyncio.sleep while holding self._lock. If
    that lock is a threading.Lock, a second concurrent caller blocks the only
    event-loop thread in .acquire() while the holder can never be resumed to
    release it -> permanent deadlock at 0% CPU (every request, incl. /health,
    hangs). With an asyncio.Lock contenders yield cooperatively and all complete.

    Run inside a worker thread so the bug fails this test cleanly (the main
    thread's join times out) instead of hanging the suite on the frozen loop.
    """
    state = tmp_path / "rate-limit.state"
    done = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        async def run() -> None:
            limiter = DistributedRateLimiter(
                requests_per_second=20.0,  # delay=0.05s, so later calls must sleep
                shared_state_file=str(state),
            )
            # Prime the state so the concurrent batch below must await
            # asyncio.sleep (the deadlock trigger) while contending for the lock.
            await limiter.wait_if_needed()
            await asyncio.gather(*(limiter.wait_if_needed() for _ in range(10)))

        try:
            asyncio.run(run())
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    finished = done.wait(timeout=10.0)

    assert finished, (
        "wait_if_needed deadlocked the event loop — a sync lock is held across "
        "an await (use asyncio.Lock, not threading.Lock)"
    )
    assert not errors, f"wait_if_needed raised under concurrency: {errors!r}"
