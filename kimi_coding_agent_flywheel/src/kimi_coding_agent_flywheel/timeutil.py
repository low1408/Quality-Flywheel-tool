"""Timestamp helpers shared by backward-compatible worker models."""

from __future__ import annotations

from datetime import UTC, datetime


def naive_utc_now() -> datetime:
    """Return naive UTC while preserving the worker's existing JSON contract."""

    return datetime.now(UTC).replace(tzinfo=None)
