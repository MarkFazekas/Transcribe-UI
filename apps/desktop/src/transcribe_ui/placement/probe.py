"""File-system probe of the placement planner: the only I/O a plan needs (architecture §3.5).

Injected, so plans for the Windows rules are tested on any OS with a fake.
"""

import os
import stat
from pathlib import Path
from typing import Literal, Protocol

# What is at a target path: nothing, a file (or a link to one), or something else (a folder).
type TargetKind = Literal["missing", "file", "other"]


class PlacementProbe(Protocol):
    """Read-only questions the planner asks the file system."""

    def folder_exists(self, path: str) -> bool:
        """Return whether the path is an existing folder."""
        ...

    def target_kind(self, path: str) -> TargetKind:
        """Return what is at a target path."""
        ...

    def resolve(self, path: str) -> str:
        """Return the absolute path with symbolic links resolved (the path need not exist)."""
        ...


class OsPlacementProbe:
    """The probe of the running OS."""

    def folder_exists(self, path: str) -> bool:
        """Return whether the path is an existing folder."""
        return Path(path).is_dir()

    def target_kind(self, path: str) -> TargetKind:
        """Return what is at a target path; a link to a file counts as a file."""
        try:
            mode = Path(path).lstat().st_mode
        except FileNotFoundError:
            return "missing"
        except OSError:
            # A file where a folder of the path should be, or no permission: nothing can go there.
            return "other"
        if stat.S_ISREG(mode):
            return "file"
        is_link = stat.S_ISLNK(mode)
        return "file" if is_link and Path(path).is_file() else "other"

    def resolve(self, path: str) -> str:
        """Return the path with symbolic links resolved."""
        return os.path.realpath(path)
