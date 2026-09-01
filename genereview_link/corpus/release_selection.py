"""Pure exhaustive selection of an identity-aware corpus release suffix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseSlot:
    suffix: int
    release: str | None
    tag: bool
    immutable: bool


def select_release_id(source_date: str, slots: list[ReleaseSlot]) -> tuple[str, bool]:
    """Choose the first wholly free slot; protected publication alone decides no-op."""
    occupied = {slot.suffix for slot in slots if slot.release is not None or slot.tag}
    suffix = 1
    while suffix in occupied:
        suffix += 1
    return f"{source_date}-r{suffix}", False


__all__ = ["ReleaseSlot", "select_release_id"]
