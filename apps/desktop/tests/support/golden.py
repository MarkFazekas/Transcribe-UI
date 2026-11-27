"""Golden files: compared byte for byte, regenerated with `UPDATE_SNAPSHOTS=1`.

On a rebase conflict in a golden or snapshot file, regenerate it; never merge it by hand.
"""

import os
from pathlib import Path

# Folder of the golden files.
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"
# Folder of the JSON Schema snapshots.
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"


def updating() -> bool:
    """Return whether golden files and snapshots are being regenerated."""
    return os.environ.get("UPDATE_SNAPSHOTS") == "1"


def assert_matches_file(path: Path, text: str) -> None:
    """Assert the text equals the file's content (written first when updating)."""
    rendered = text.encode("utf-8")
    if updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered)
    assert rendered == path.read_bytes(), f"{path.name} differs; regenerate it with UPDATE_SNAPSHOTS=1"


def assert_golden(name: str, text: str) -> None:
    """Assert the text equals `golden/<name>`."""
    assert_matches_file(GOLDEN_DIR / name, text)
