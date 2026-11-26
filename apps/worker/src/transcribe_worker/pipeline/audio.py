"""Local audio handling: a size-bounded download cache and the ffmpeg decode to 16 kHz mono."""

import logging
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path

from transcribe_worker.pipeline.constants import DECODE_ERROR_CHARS, DECODE_TIMEOUT_S, SAMPLE_RATE_HZ
from transcribe_worker.pipeline.storage import ObjectStore

logger = logging.getLogger(__name__)


class DecodeError(Exception):
    """ffmpeg could not decode an audio file."""


class FfmpegMissingError(RuntimeError):
    """ffmpeg is not installed (an image problem, not the file's: the job is retried later)."""


class AudioCache:
    """Downloaded audio objects on the container disk, least recently used evicted first.

    An item with several languages is needed once per language pass; keeping its audio saves
    a download, and an evicted file is simply downloaded again. Used from one thread only.
    """

    def __init__(self, store: ObjectStore, folder: Path, limit_bytes: int) -> None:
        """Use `folder` (created if missing) for at most `limit_bytes` of audio."""
        self._store = store
        self._folder = folder
        self._limit_bytes = limit_bytes
        self._entries: OrderedDict[str, tuple[Path, int]] = OrderedDict()
        folder.mkdir(parents=True, exist_ok=True)

    def fetch(self, key: str) -> Path:
        """Return a local copy of the object, downloading it if it is not cached."""
        cached = self._entries.get(key)
        if cached is not None and cached[0].exists():
            self._entries.move_to_end(key)
            return cached[0]
        target = self._folder / key.replace("/", "__")
        partial = target.with_name(f"{target.name}.part")
        try:
            self._store.download(key, partial)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(target)
        size = target.stat().st_size
        self._entries[key] = (target, size)
        self._evict(keep=key)
        return target

    @property
    def used_bytes(self) -> int:
        """Return the bytes currently cached."""
        return sum(size for _, size in self._entries.values())

    def _evict(self, keep: str) -> None:
        while self.used_bytes > self._limit_bytes and len(self._entries) > 1:
            oldest = next(iter(self._entries))
            if oldest == keep:
                break
            path, _ = self._entries.pop(oldest)
            path.unlink(missing_ok=True)
            logger.info("evicted %s from the audio cache", oldest)


def decode_to_wav(source: Path, target: Path) -> None:
    """Decode the first audio stream of `source` to a 16 kHz mono 16-bit WAV file.

    Raises:
        FfmpegMissingError: ffmpeg is not on PATH.
        DecodeError: ffmpeg failed or timed out; the message has ffmpeg's error.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg is not installed"
        raise FfmpegMissingError(msg)
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    argv += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", f"{SAMPLE_RATE_HZ}", "-c:a", "pcm_s16le"]
    argv += ["-f", "wav", str(target)]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=DECODE_TIMEOUT_S, check=False)  # noqa: S603 - fixed argv, no shell
    except subprocess.TimeoutExpired as error:
        msg = f"ffmpeg did not finish within {DECODE_TIMEOUT_S} s"
        raise DecodeError(msg) from error
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip()
        stderr = stderr[-DECODE_ERROR_CHARS:]
        msg = f"ffmpeg failed (exit {completed.returncode}): {stderr or 'no output'}"
        raise DecodeError(msg)
