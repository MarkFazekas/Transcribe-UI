"""Runs the Transcriber in a child process, so a hung or crashed job cannot stop the run.

A CUDA call cannot be interrupted from Python, so the per-job timeout kills the whole engine
process; the next job starts a fresh one (models load again from the image). The child is
started with "spawn" (no inherited threads or CUDA state) and builds the Transcriber from a
"module:callable" path.
"""

import importlib
import logging
import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path
from typing import TYPE_CHECKING, Any

from transcribe_contracts.classes import EngineInfo
from transcribe_worker.engine.classes import Transcript
from transcribe_worker.engine.constants import (
    MESSAGE_ERROR,
    MESSAGE_OK,
    MESSAGE_READY,
    MESSAGE_START_FAILED,
    STOP_TIMEOUT_S,
)
from transcribe_worker.engine.interface import Transcriber

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess

logger = logging.getLogger(__name__)


class EngineError(Exception):
    """The engine could not transcribe a file (failure, crash, or timeout)."""


class EngineProcess:
    """A Transcriber in a child process, restarted after a crash or a timeout."""

    def __init__(self, factory: str, start_timeout_s: float) -> None:
        """Remember how to build the engine; nothing starts until `start` or the first job."""
        self._factory = factory
        self._start_timeout_s = start_timeout_s
        self._context = multiprocessing.get_context("spawn")
        self._process: BaseProcess | None = None
        self._conn: Connection | None = None
        self._settings: dict[bool, EngineInfo] = {}

    def start(self) -> None:
        """Start the engine process and wait until its models are loaded.

        Raises:
            EngineError: The engine failed to start or did not get ready in time.
        """
        parent_conn, child_conn = self._context.Pipe()
        process = self._context.Process(target=serve, args=(child_conn, self._factory))
        process.daemon = True
        process.start()
        child_conn.close()
        self._process = process
        self._conn = parent_conn
        kind, payload = self._receive(self._start_timeout_s, "start")
        if kind != MESSAGE_READY:
            self._kill()
            msg = f"engine did not start: {payload}"
            raise EngineError(msg)
        self._settings = {
            careful: EngineInfo.model_validate(settings)
            for careful, settings in zip((False, True), payload, strict=True)
        }
        logger.info("engine ready: %s", self._settings[False].model_dump())

    def settings(self, *, careful: bool) -> EngineInfo:
        """Return the engine settings of a mode (known once the engine has started)."""
        if not self._settings:
            self.start()
        return self._settings[careful]

    def transcribe(self, audio: Path, language: str, *, careful: bool, timeout_s: float) -> Transcript:
        """Transcribe in the engine process; restart it first if it is not running.

        Raises:
            EngineError: The job failed, the engine crashed, or the timeout passed (the engine
                process is then killed and restarted for the next job).
        """
        if self._conn is None:
            self.start()
        assert self._conn is not None  # set by start(), which raises otherwise
        self._conn.send((str(audio), language, careful))
        kind, payload = self._receive(timeout_s, "job")
        if kind == MESSAGE_OK:
            return Transcript.model_validate(payload)
        raise EngineError(str(payload))

    def close(self) -> None:
        """Ask the engine process to exit, and kill it if it does not."""
        if self._conn is not None and self._process is not None:
            try:
                self._conn.send(None)
            except OSError:
                logger.warning("engine pipe already closed")
            self._process.join(STOP_TIMEOUT_S)
        self._kill()

    def _receive(self, timeout_s: float, what: str) -> tuple[str, Any]:
        assert self._conn is not None  # only called while a process runs
        try:
            ready = self._conn.poll(timeout_s)
        except OSError:
            ready = False
        if not ready:
            self._kill()
            return MESSAGE_ERROR, f"engine {what} timed out after {timeout_s:.0f} s; engine restarted"
        try:
            message: tuple[str, Any] = self._conn.recv()
        except (EOFError, OSError):
            exit_code = self._exit_code()
            self._kill()
            return MESSAGE_ERROR, f"engine process died during the {what} (exit code {exit_code})"
        return message

    def _exit_code(self) -> int | None:
        if self._process is None:
            return None
        self._process.join(STOP_TIMEOUT_S)
        return self._process.exitcode

    def _kill(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.kill()
            self._process.join(STOP_TIMEOUT_S)
        if self._conn is not None:
            self._conn.close()
        self._process = None
        self._conn = None


def serve(conn: Connection, factory: str) -> None:
    """Child process main loop: build the engine, then answer jobs until None arrives."""
    logging.basicConfig(level=logging.INFO)
    try:
        transcriber = _build(factory)
        settings = _settings_of(transcriber)
    except Exception as error:  # noqa: BLE001 - any start failure is reported to the parent
        conn.send((MESSAGE_START_FAILED, describe(error)))
        return
    conn.send((MESSAGE_READY, settings))
    while True:
        request = conn.recv()
        if request is None:
            return
        conn.send(_answer(transcriber, request))


type JobRequest = tuple[str, str, bool]


def describe(error: BaseException) -> str:
    """Return an error as 'Type: message' for job results and logs."""
    return f"{type(error).__name__}: {error}"


def _answer(transcriber: Transcriber, request: JobRequest) -> tuple[str, Any]:
    audio, language, careful = request
    try:
        transcript = transcriber.transcribe(Path(audio), language, careful=careful)
    except Exception as error:
        logger.exception("transcription failed")
        return MESSAGE_ERROR, describe(error)
    return MESSAGE_OK, transcript.model_dump()


def _settings_of(transcriber: Transcriber) -> list[dict[str, Any]]:
    return [transcriber.settings(careful=careful).model_dump() for careful in (False, True)]


def _build(factory: str) -> Transcriber:
    module_name, _, attribute = factory.partition(":")
    builder = getattr(importlib.import_module(module_name), attribute)
    transcriber: Transcriber = builder()
    return transcriber
