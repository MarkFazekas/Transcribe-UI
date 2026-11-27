"""Listing one folder for the scan: which entries are shown and what they are (architecture §3.2).

Classification uses the directory entry's type and the name first (no `stat` on Linux); `stat`
runs only where the OS keeps hidden flags in it (Windows, where it is free, and macOS/BSD) and
for media files (size, mtime). Entries that vanish during the scan are skipped silently.
"""

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from transcribe_ui.api.errors import DomainError
from transcribe_ui.discovery.classes import ScanWarning, ScanWarningCode
from transcribe_ui.discovery.constants import (
    APPLE_DOUBLE_PREFIX,
    ERROR_ROOT_UNREADABLE,
    HIDDEN_FILE_ATTRIBUTE,
    HIDDEN_NAME_PREFIX,
    STAT_HAS_HIDDEN_FLAGS,
)
from transcribe_ui.discovery.rules import display_order

# What an entry the scan keeps is.
type EntryKind = Literal["folder", "file"]


def _has_hidden_flag(entry_stat: os.stat_result) -> bool:
    attributes = getattr(entry_stat, "st_file_attributes", 0)  # Windows only.
    flags = getattr(entry_stat, "st_flags", 0)  # macOS and BSD only.
    return bool(attributes & HIDDEN_FILE_ATTRIBUTE) or bool(flags & stat.UF_HIDDEN)


def _is_valid_unicode(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _shown(name: str) -> str:
    """Return a name with what is not valid Unicode replaced by U+FFFD."""
    return os.fsencode(name).decode("utf-8", errors="replace")


@dataclass
class EntryReader:
    """Lists folders and classifies entries; collects the warnings."""

    include_hidden: bool
    warnings: list[ScanWarning] = field(default_factory=list)

    def warn(self, code: ScanWarningCode, path: str, message: str) -> None:
        """Record a warning."""
        self.warnings.append(ScanWarning(code=code, path=path, message=message))

    def entries(self, path: str, *, is_root: bool) -> list[os.DirEntry[str]]:
        """Return the folder's entries in display order.

        Raises:
            DomainError: If the scan root itself cannot be listed.
        """
        try:
            with os.scandir(path) as listing:
                return sorted(listing, key=lambda entry: display_order(entry.name))
        except OSError as error:
            if is_root:
                raise DomainError(ERROR_ROOT_UNREADABLE, "The folder to scan could not be read.") from error
            if not isinstance(error, FileNotFoundError):
                self.warn("unreadable", path, "The folder could not be read.")
            return []

    def kind_of(self, entry: os.DirEntry[str]) -> EntryKind | None:
        """Return what the entry is, or None when the scan leaves it out."""
        if not self._name_shown(entry):
            return None
        try:
            return self._entry_kind(entry)
        except FileNotFoundError:
            return None
        except OSError:
            self.warn("unreadable", entry.path, "The entry could not be read.")
            return None

    def _name_shown(self, entry: os.DirEntry[str]) -> bool:
        name = entry.name
        if not _is_valid_unicode(name):
            message = "The name is not valid Unicode; rename it to include it."
            folder = Path(entry.path).parent
            self.warn("invalid_name", str(folder / _shown(name)), message)
            return False
        if name.startswith(APPLE_DOUBLE_PREFIX):
            return False
        return self.include_hidden or not name.startswith(HIDDEN_NAME_PREFIX)

    def _entry_kind(self, entry: os.DirEntry[str]) -> EntryKind | None:
        if not self.include_hidden and STAT_HAS_HIDDEN_FLAGS and _has_hidden_flag(entry.stat(follow_symlinks=False)):
            return None
        if entry.is_symlink() or entry.is_junction():
            self.warn("link_skipped", entry.path, "Links are not followed.")
            return None
        if entry.is_dir(follow_symlinks=False):
            return "folder"
        return "file" if entry.is_file(follow_symlinks=False) else None
