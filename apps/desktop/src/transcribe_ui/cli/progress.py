"""Progress output of the CLI: one JSON line per event on stderr."""

import threading
from typing import BinaryIO

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.cli.constants import LINE_END


class NdjsonProgress:
    """Progress sink writing one JSON line per event; safe to call from several threads."""

    def __init__(self, stream: BinaryIO) -> None:
        """Write to this binary stream."""
        self._stream = stream
        self._lock = threading.Lock()

    def emit(self, event: ProgressEvent) -> None:
        """Write the event as one line and flush."""
        line = event.model_dump_json().encode("utf-8") + LINE_END
        with self._lock:
            self._stream.write(line)
            self._stream.flush()
