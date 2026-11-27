"""Reading and writing custom plan files (`import_custom_plan`, `export_custom_plan`; architecture §3.5)."""

import os
import stat
from pathlib import Path
from typing import BinaryIO

from transcribe_ui.placement.classes import CustomPlan
from transcribe_ui.placement.constants import (
    CUSTOM_PLAN_INDENT,
    MAX_CUSTOM_PLAN_BYTES,
    NOT_A_FILE,
    PLAN_READ_FLAGS,
    TOO_LARGE,
)
from transcribe_ui.placement.errors import CustomPlanUnreadableError, CustomPlanUnwritableError
from transcribe_ui.placement.writer import write_atomically


def write_plan(path: Path, plan: CustomPlan) -> None:
    """Write the plan as JSON through a temp file (an existing file is replaced).

    Raises:
        CustomPlanUnwritableError: The file could not be written.
    """
    text = plan.model_dump_json(indent=CUSTOM_PLAN_INDENT)
    try:
        write_atomically(path, f"{text}\n".encode())
    except OSError as error:
        raise CustomPlanUnwritableError(type(error).__name__) from None


def read_plan(path: Path) -> bytes:
    """Return the plan file's bytes; only a regular file is read (a FIFO would block under the batch lock).

    Raises:
        CustomPlanUnreadableError: The file cannot be opened or read, is not a regular file or is too large.
    """
    try:
        descriptor = os.open(path, PLAN_READ_FLAGS)
    except OSError as error:
        raise CustomPlanUnreadableError(type(error).__name__) from None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CustomPlanUnreadableError(NOT_A_FILE)
        document = _read_limited(stream)
    if len(document) > MAX_CUSTOM_PLAN_BYTES:
        raise CustomPlanUnreadableError(TOO_LARGE)
    return document


def _read_limited(stream: BinaryIO) -> bytes:
    """Return up to one byte more than the limit (so a larger file shows).

    Raises:
        CustomPlanUnreadableError: The file could not be read.
    """
    try:
        return stream.read(MAX_CUSTOM_PLAN_BYTES + 1)
    except OSError as error:
        raise CustomPlanUnreadableError(type(error).__name__) from None
