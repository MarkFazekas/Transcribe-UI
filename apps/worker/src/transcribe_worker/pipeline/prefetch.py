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
from transcribe_worker.pipeline.audio import AudioCache
from transcribe_worker.pipeline.constants import PREFETCH_PUT_POLL_S
from transcribe_worker.pipeline.plan import PlannedJob

logger = logging.getLogger(__name__)

type Decoder = Callable[[Path, Path], None]


@dataclass(frozen=True)
class PreparedAudio:
    """Decoded audio of a job, or why it could not be prepared."""

    planned: PlannedJob
    wav: Path | None
    problem: str | None

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
        """Stop preparing and delete decoded files nobody took."""
        self._stop.set()
        self._drain()
        self._thread.join()
        self._drain()

    def take(self) -> PreparedAudio:
        """Return the next job's audio, waiting until it is prepared."""
        return self._ready.get()

    def _work(self) -> None:
        for planned in self._jobs:
            prepared = self._prepare(planned)
            while not self._put(prepared):
                if self._stop.is_set():
                    prepared.discard()
                    return

    def _put(self, prepared: PreparedAudio) -> bool:
        try:
            self._ready.put(prepared, timeout=PREFETCH_PUT_POLL_S)
        except queue.Full:
            return False
        return True

    def _prepare(self, planned: PlannedJob) -> PreparedAudio:
        name = planned.json_key.replace("/", "__").removesuffix(JSON_SUFFIX)
        wav = self._wav_dir / f"{name}.wav"
        try:
            source = self._cache.fetch(planned.audio.object_key)
            self._decode(source, wav)
        except Exception as error:
            logger.warning("could not prepare %s", planned.audio.object_key, exc_info=True)
            wav.unlink(missing_ok=True)
            return PreparedAudio(planned=planned, wav=None, problem=describe(error))
        return PreparedAudio(planned=planned, wav=wav, problem=None)

    def _drain(self) -> None:
        while True:
            try:
                self._ready.get_nowait().discard()
            except queue.Empty:
                return
