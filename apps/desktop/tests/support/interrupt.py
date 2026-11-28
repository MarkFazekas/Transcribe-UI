"""Interrupting `run_upload` at a chosen external call: a simulated kill, or the cancel token (plan m5 M5.10).

A `Tripwire` counts the calls the upload makes to R2 (through `TripStore`) and to the media tools
(through `TripMedia`); at call number `at` it fires before or after the call. Firing raises
`Crash`, a `BaseException` the backend does not catch (like a killed process: what was committed
stays, nothing else runs), or sets the request's cancel flag.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from support.batches import FILM
from support.upload import change_source
from transcribe_ui.api.ports import CancelFlag, CancelToken, Ports, RequestContext
from transcribe_ui.credentials.classes import R2Credentials
from transcribe_ui.media.classes import ConvertedAudio, ProbeResult
from transcribe_ui.media.ports import MediaTools
from transcribe_ui.runs.cloud import open_store
from transcribe_ui.storage.client import ClientLimits, r2_client
from transcribe_ui.storage.multipart import UploadProgress
from transcribe_ui.storage.r2 import ObjectInfo, ObjectStore

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client


class Crash(BaseException):  # noqa: WPS418 -- like a real kill, it must pass the backend's `except Exception`
    """A simulated kill of the app in the middle of a request."""


@dataclass
class Tripwire:
    """Counts external calls; fires at call `at` (None: never), before it or after it."""

    at: int | None = None
    after: bool = False
    # Set at the trip instead of crashing (None: crash).
    cancel: CancelFlag | None = None
    calls: int = 0
    fired: bool = False

    def around[ResultT](self, call: Callable[[], ResultT]) -> ResultT:
        """Make the call, firing before or after it when it is the chosen one."""
        self.calls += 1
        chosen = self.calls == self.at
        if chosen and not self.after:
            self._fire()
        outcome = call()
        if chosen and self.after:
            self._fire()
        return outcome

    def _fire(self) -> None:
        self.fired = True
        if self.cancel is None:
            raise Crash
        self.cancel.cancel()


@dataclass
class TripStore:  # noqa: WPS214 -- one method per `ObjectStore` operation.
    """An `ObjectStore` whose every call goes through the tripwire."""

    inner: ObjectStore
    wire: Tripwire

    def check_access(self) -> None:
        """Pass on."""
        self.wire.around(self.inner.check_access)

    def head(self, key: str) -> ObjectInfo | None:
        """Pass on."""
        return self.wire.around(lambda: self.inner.head(key))

    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        """Pass on."""
        return self.wire.around(lambda: self.inner.list_objects(prefix))

    def read(self, key: str) -> bytes | None:
        """Pass on."""
        return self.wire.around(lambda: self.inner.read(key))

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Pass on."""
        self.wire.around(lambda: self.inner.write(key, body, content_type))

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Pass on."""
        return self.wire.around(lambda: self.inner.write_new(key, body, content_type))

    def copy(self, source_key: str, target_key: str) -> None:
        """Pass on."""
        self.wire.around(lambda: self.inner.copy(source_key, target_key))

    def delete(self, key: str) -> None:
        """Pass on."""
        self.wire.around(lambda: self.inner.delete(key))

    def abort_uploads(self, prefix: str) -> int:
        """Pass on."""
        return self.wire.around(lambda: self.inner.abort_uploads(prefix))

    def count_uploads(self, prefix: str) -> int:
        """Pass on."""
        return self.wire.around(lambda: self.inner.count_uploads(prefix))

    def upload_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        progress: UploadProgress,
        cancel: CancelToken,
    ) -> int:
        """Pass on."""
        return self.wire.around(lambda: self.inner.upload_file(key, source, content_type, progress, cancel))


@dataclass
class TripMedia:
    """Media tools whose conversions and extractions go through the tripwire (probes do not)."""

    inner: MediaTools
    wire: Tripwire = field(default_factory=Tripwire)

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Pass on (not counted: the review probes, the upload does not)."""
        return self.inner.probe(source, cancel)

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Pass on."""
        return self.wire.around(lambda: self.inner.convert(source, track_index, output_base, cancel))

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Pass on."""
        return self.wire.around(lambda: self.inner.extract(source, stream_index, cancel))


def trip_media(ports: Ports) -> Ports:
    """Wrap the ports' media tools in `TripMedia` (its wire is replaced per attempt), with shared R2 clients.

    Building a boto3 client loads the S3 service model (the app builds one per request); the
    interruption tests send hundreds of requests, so they reuse one client per bucket.
    """
    return replace(ports, media=TripMedia(ports.media), r2_clients=_shared_client)


class _ClientCache:
    """R2 clients of the interruption tests, by bucket (single-threaded tests only)."""

    def __init__(self) -> None:
        """Start empty."""
        self.clients: dict[str, S3Client] = {}

    def __call__(self, credentials: R2Credentials, limits: ClientLimits) -> "S3Client":
        """Return the bucket's client, built on first use."""
        if credentials.bucket not in self.clients:
            self.clients[credentials.bucket] = r2_client(credentials, limits)
        return self.clients[credentials.bucket]


# The shared clients.
_shared_client = _ClientCache()


# Builds the object store `run_upload` uses from the one `open_store` opened.
type StoreWrap = Callable[[ObjectStore], ObjectStore]


def patch_upload_store(monkeypatch: pytest.MonkeyPatch, wrap: StoreWrap) -> None:
    """Make `run_upload` (and its manifest step) use a wrapped object store."""
    monkeypatch.setattr("transcribe_ui.upload.services.open_store", partial(_wrapped_store, wrap))


def _wrapped_store(wrap: StoreWrap, context: RequestContext) -> ObjectStore:
    return wrap(open_store(context))


@dataclass(frozen=True)
class EditDuringConversion:
    """Media tools that edit the film (as a user would) while its English track (1) is converted."""

    inner: MediaTools
    root: Path

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Pass on."""
        return self.inner.probe(source, cancel)

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Pass on."""
        return self.inner.extract(source, stream_index, cancel)

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Convert, editing the film during its second track."""
        converted = self.inner.convert(source, track_index, output_base, cancel)
        if source.name == FILM and track_index == 1:
            change_source(self.root, FILM)
        return converted
