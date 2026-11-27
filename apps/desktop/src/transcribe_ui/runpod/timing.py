"""Timing of the Runpod client: waits between attempts, limits, clock and cancel check."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from transcribe_ui.runpod.constants import (
    CONNECT_TIMEOUT_S,
    MAX_ATTEMPTS,
    READ_TIMEOUT_S,
    RETRY_AFTER_MAX_S,
    WAIT_SLICE_S,
)

# The current time, timezone-aware (dates in `Retry-After` are measured against it).
type Clock = Callable[[], datetime]
# Returns True once the request was cancelled.
type Stopped = Callable[[], bool]
# Waits that many seconds, returning early once `Stopped` says so (tests record the waits).
type Wait = Callable[[float, Stopped], None]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def never_stopped() -> bool:
    """Return False: a client without a cancel token."""
    return False


def wait_unless_stopped(seconds: float, stopped: Stopped) -> None:
    """Sleep in short slices, returning early once `stopped` says so."""
    deadline = time.monotonic() + seconds
    while not stopped():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, WAIT_SLICE_S))


@dataclass(frozen=True)
class Timing:
    """How the client waits between attempts, its limits, its clock and its cancel check."""

    wait: Wait = wait_unless_stopped
    now: Clock = utc_now
    stopped: Stopped = never_stopped
    # Attempts per call, the first included.
    attempts: int = MAX_ATTEMPTS
    connect_timeout_s: float = CONNECT_TIMEOUT_S
    read_timeout_s: float = READ_TIMEOUT_S
    # Longest wait between two attempts (backoff and `Retry-After` alike).
    wait_max_s: float = RETRY_AFTER_MAX_S
