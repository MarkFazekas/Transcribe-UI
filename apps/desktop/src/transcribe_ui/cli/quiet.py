"""Keeps log records and warnings off stderr, which carries only NDJSON progress.

M5.2 replaces this with the log file in the user log dir.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def logs_off_stderr() -> Iterator[None]:
    """Route warnings into logging and give the root logger a handler that drops every record.

    Without a handler, `logging` would print through its last-resort handler to stderr. Both
    changes are undone when the block ends (tests run the CLI in-process).
    """
    root = logging.getLogger()
    null_handler = logging.NullHandler()
    root.addHandler(null_handler)
    logging.captureWarnings(capture=True)
    try:
        yield
    finally:
        logging.captureWarnings(capture=False)
        root.removeHandler(null_handler)
