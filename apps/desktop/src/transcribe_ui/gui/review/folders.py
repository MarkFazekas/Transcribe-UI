"""The folder filter's choices and the folder column of the review page (architecture §3.1 "GUI" "Review and upload").

Qt-free and presentation only: it lists the folders below the job's folder that hold a file in
review, written below the job's folder, with the job's own path flavour. Whether a file is in
a folder stays the backend's to decide (`review_batch` compares with the path rules).
"""

from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from transcribe_ui.batches.constants import ITEM_REVIEW_PHASES
from transcribe_ui.batches.responses import BatchDetail
from transcribe_ui.gui.review.constants import WINDOWS_FLAVOR
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class FolderChoice:
    """A folder the filter offers: its path for the request (None: every folder) and its label."""

    path: str | None
    label: str


def _pure(detail: BatchDetail, path: str) -> PurePath:
    if detail.path_flavor == WINDOWS_FLAVOR:
        return PureWindowsPath(path)
    return PurePosixPath(path)


def relative_folder(detail: BatchDetail, path: str) -> str:
    """Return the folder of a file below the job's folder ("" for the job's folder itself)."""
    root = _pure(detail, detail.scan_root)
    parent = _pure(detail, path).parent
    if parent == root:
        return ""
    if root in parent.parents:
        return str(parent.relative_to(root))
    return str(parent)


def _label_key(choice: FolderChoice) -> str:
    return choice.label.casefold()


def folder_choices(detail: BatchDetail) -> tuple[FolderChoice, ...]:
    """Return "All folders", then every folder below the job's folder that holds a file in review, by name."""
    root = _pure(detail, detail.scan_root)
    found: dict[str, str] = {}
    for member in detail.items:
        if member.phase not in ITEM_REVIEW_PHASES:
            continue
        folder = _pure(detail, member.path).parent
        while folder != root and root in folder.parents:
            found[str(folder)] = str(folder.relative_to(root))
            folder = folder.parent
    choices = [FolderChoice(path, label) for path, label in found.items()]
    choices.sort(key=_label_key)
    return (FolderChoice(None, tr("All folders")), *choices)
