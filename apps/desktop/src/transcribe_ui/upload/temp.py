"""The temp folder of a batch's upload: converted audio, one file at a time (plan m5 §1 "Upload").

Each batch has its own folder under the cache dir, so uploads of different batches never touch
each other's files. A conversion writes a uniquely named `.part` file there and renames it into
place (`media/convert.py`); the finished file is deleted once its object is recorded. Whatever a
crash or a killed app left behind (finished files, partial files of an orphaned ffmpeg) is
removed when the next `run_upload` of the batch starts, and by the batch's cloud cleanup.
"""

import logging
from pathlib import Path

from transcribe_ui.state.paths import AppPaths
from transcribe_ui.upload.constants import TEMP_FOLDER

logger = logging.getLogger(__name__)


def temp_dir(paths: AppPaths, batch_id: str) -> Path:
    """Return the batch's temp folder (not created here: the conversion creates it)."""
    return paths.cache_dir / TEMP_FOLDER / batch_id


def clear_temp(paths: AppPaths, batch_id: str) -> None:
    """Delete every file left in the batch's temp folder; a file that cannot be deleted is logged and kept."""
    folder = temp_dir(paths, batch_id)
    if not folder.is_dir():
        return
    for left in folder.iterdir():
        remove_temp(left)


def remove_temp(path: Path) -> None:
    """Delete one temp file; a failure is logged (the next start of the batch's upload tries again)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete the temp file %s", path, exc_info=True)
