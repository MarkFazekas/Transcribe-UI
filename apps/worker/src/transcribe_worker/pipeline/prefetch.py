"""Bounded prefetch: download and decode the next audio files while the GPU works."""

import logging
import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from transcribe_contracts.constants import JSON_SUFFIX
from transcribe_worker.engine.host import describe
from transcribe_worker.pipeline.audio import AudioCache, DecodeError
from transcribe_worker.pipeline.constants import PREFETCH_PUT_POLL_S, THREAD_STOP_TIMEOUT_S
from transcribe_worker.pipeline.plan import PlannedJob
from transcribe_worker.pipeline.storage import ObjectMissingError

logger = logging.getLogger(__name__)

type Decoder = Callable[[Path, Path], None]


class PrefetchError(RuntimeError):
    """The prefetch thread stopped before preparing every job (a bug; the run must stop)."""


@dataclass(frozen=True)
class PreparedAudio:
    """Decoded audio of a job, or why it could not be prepared.

    `retry_later` marks a problem that is not the file's fault (storage or disk trouble): the
    job gets no result, so the next run tries it again. A context manager that deletes the
    decoded file on exit.
    """

    planned: PlannedJob
    wav: Path | None
    problem: str | None
    retry_later: bool = False

    def __enter__(self) -> Self:
        """Use the prepared audio."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Delete the decoded file."""
        self.discard()

    def discard(self) -> None:
        """Delete the decoded file."""
        if self.wav is not None:
            self.wav.unlink(missing_ok=True)


class Prefetcher:
    """Prepares jobs in order in a background thread, at most `depth` ahead of the consumer."""

    def __init__(  # noqa: WPS211 - each argument is a distinct, injected dependency
        self,
        jobs: Sequence[PlannedJob],
        cache: AudioCache,
        wav_dir: Path,
        decode: Decoder,
        depth: int,
    ) -> None:
        """Prepare `jobs` (in this order) into `wav_dir`; nothing runs before `__enter__`."""
        self._jobs = jobs
        self._cache = cache
        self._wav_dir = wav_dir
        self._decode = decode
        self._ready: queue.Queue[PreparedAudio] = queue.Queue(maxsize=depth)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._work, name="prefetch", daemon=True)

    def __enter__(self) -> Self:
        """Start preparing."""
        self._wav_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop preparing (waiting a bounded time) and delete decoded files nobody took."""
        self._stop.set()
        self._drain()
        self._thread.join(THREAD_STOP_TIMEOUT_S)
        if self._thread.is_alive():
            logger.warning("prefetch thread still busy at the end of the run; abandoned")
        self._drain()

    def take(self) -> PreparedAudio:
        """Return the next job's audio, waiting until it is prepared.

        Raises:
            PrefetchError: The prefetch thread ended without preparing it.
        """
        while True:
            try:
                return self._ready.get(timeout=PREFETCH_PUT_POLL_S)
            except queue.Empty:
                self._check_alive()

    def _check_alive(self) -> None:
        if self._thread.is_alive() or not self._ready.empty():
            return
        msg = "the prefetch thread stopped unexpectedly"
        raise PrefetchError(msg)

    def _work(self) -> None:
        for planned in self._jobs:
            if self._stop.is_set():
                return
            if not self._offer(self._prepare(planned)):
                return

    def _offer(self, prepared: PreparedAudio) -> bool:
        """Queue the prepared audio; False (and the file deleted) when stopping."""
        while not self._stop.is_set():
            try:
                self._ready.put(prepared, timeout=PREFETCH_PUT_POLL_S)
            except queue.Full:
                continue
            return True
        prepared.discard()
        return False

    def _prepare(self, planned: PlannedJob) -> PreparedAudio:
        name = planned.json_key.replace("/", "__").removesuffix(JSON_SUFFIX)
        wav = self._wav_dir / f"{name}.wav"
        try:
            self._decode(self._cache.fetch(planned.audio.object_key), wav)
        except (ObjectMissingError, DecodeError) as error:
            logger.warning("cannot use %s: %s", planned.audio.object_key, error)
            wav.unlink(missing_ok=True)
            return PreparedAudio(planned=planned, wav=None, problem=_problem(error))
        except Exception as error:
            logger.warning("could not prepare %s; left for the next run", planned.audio.object_key, exc_info=True)
            wav.unlink(missing_ok=True)
            return PreparedAudio(planned=planned, wav=None, problem=describe(error), retry_later=True)
        return PreparedAudio(planned=planned, wav=wav, problem=None)

    def _drain(self) -> None:
        while True:
            try:
                self._ready.get_nowait().discard()
            except queue.Empty:
                return


def _problem(error: Exception) -> str:
    if isinstance(error, ObjectMissingError):
        return f"the audio object is missing from storage: {error}"
    return str(error)
