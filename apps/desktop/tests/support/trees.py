"""Generated media trees for the discovery tests: empty files with fixed mtimes (plan m5 §3 M5.3)."""

import os
from collections.abc import Iterable
from pathlib import Path

# mtime of every generated file (27 November 2026, 12:00 UTC), so golden files match on every OS.
FIXED_MTIME_NS = 1_795_780_800_000_000_000


def build_tree(root: Path, files: Iterable[str]) -> None:
    """Create each `/`-separated relative path under the root as an empty file with the fixed mtime."""
    for relative in files:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
