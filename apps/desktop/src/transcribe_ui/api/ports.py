"""Ports injected into the backend, and their production adapters (architecture §3.1).

Clock, id source, local state (its directories), progress sink and cancel token are injected
so the same request gives the same response from every frontend and in tests.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from transcribe_contracts.constants import ULID_RANDOM_BITS
from transcribe_contracts.ids import new_ulid
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.discovery.cache import ScanCache
from transcribe_ui.discovery.rules import PathRules, host_rules
from transcribe_ui.state.store import LocalState

if TYPE_CHECKING:
    # For type checking only: `media.ports` uses `CancelToken` from here, and importing the
    # ports must not load keyring and boto3.
    from transcribe_ui.credentials.store import CredentialStore
    from transcribe_ui.media.ports import MediaTools
    from transcribe_ui.storage.client import R2ClientFactory

# Returns that many random bits (`random.Random.getrandbits` signature).
type RandomBits = Callable[[int], int]


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time, timezone-aware."""
        ...


class IdSource(Protocol):
    """Source of new ULIDs (item ids, run ids)."""

    def new_id(self) -> str:
        """Return a new canonical ULID."""
        ...


class ProgressSink(Protocol):
    """Receiver of progress events; may be called from any thread."""

    def emit(self, event: ProgressEvent) -> None:
        """Take one progress event."""
        ...


class CancelToken(Protocol):
    """Cancellation flag checked by long requests between steps."""

    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        ...


class SystemClock:
    """The real clock, in UTC."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


class UlidSource:
    """ULIDs from `transcribe_contracts.ids.new_ulid`, timed by the injected clock."""

    def __init__(self, clock: Clock, randbits: RandomBits | None = None) -> None:
        """Use the clock for the time part; `randbits` (tests: a seeded generator) or `secrets` for the rest."""
        self._clock = clock
        self._randbits = randbits

    def new_id(self) -> str:
        """Return a new ULID."""
        randomness = None if self._randbits is None else self._randbits(ULID_RANDOM_BITS)
        return new_ulid(self._clock.now(), randomness)


class DiscardProgress:
    """Progress sink that drops every event."""

    def emit(self, event: ProgressEvent) -> None:
        """Drop the event."""


class CancelFlag:
    """Thread-safe cancel token that a frontend sets (the CLI on Ctrl+C)."""

    def __init__(self) -> None:
        """Start not cancelled."""
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        return self._event.is_set()


@dataclass(frozen=True)
class Ports:
    """The long-lived ports of one backend."""

    clock: Clock
    ids: IdSource
    media: "MediaTools"
    # Settings, locks and the lazily opened database, under injectable directories.
    state: LocalState
    # Keyring entries with the environment as fallback (tests: in-memory keyring, empty environment).
    credentials: "CredentialStore"
    # Builds the S3 client for R2 from the credentials (tests: the same settings, served by moto).
    r2_clients: "R2ClientFactory"
    # How the host's file system compares names; tests inject the Windows or macOS rule.
    path_rules: PathRules = field(default_factory=host_rules)
    # The latest scans, reused by `preview_selection` (process-local).
    scan_cache: ScanCache = field(default_factory=ScanCache)


@dataclass(frozen=True)
class RequestContext:
    """What a request handler gets next to its request."""

    ports: Ports
    progress: ProgressSink
    cancel: CancelToken

    def check_cancelled(self) -> None:
        """Stop the handler when cancellation was requested.

        Raises:
            CancelledError: If the cancel token is set.
        """
        if self.cancel.is_cancelled():
            raise CancelledError
