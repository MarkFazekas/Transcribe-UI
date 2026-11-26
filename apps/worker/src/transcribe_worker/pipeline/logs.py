"""Worker logging: JSON lines to stderr and to the log file uploaded with the run status.

The environment is never logged: it holds RUNPOD_API_KEY and the R2 secrets.
"""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import override

type ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message and the traceback if any.

    The traceback goes inside the JSON object, so `formatException` returns nothing for the
    base class to append after it.
    """

    @override
    def formatMessage(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        raised = record.exc_info[1] if record.exc_info else None
        if raised is not None:
            lines = traceback.format_exception(raised)
            entry["exception"] = "".join(lines)
        return json.dumps(entry, ensure_ascii=False)

    @override
    def formatException(self, exc_info: ExcInfo) -> str:
        return ""


def configure_logging(log_file: Path) -> None:
    """Send INFO and above to stderr and to `log_file` (created with its folder)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    for log_handler in handlers:
        log_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
