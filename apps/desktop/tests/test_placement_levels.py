"""The folder levels `plan_download` reports for the `folder_levels` choice (architecture §3.5; plan m6 M6.9)."""

from support.placement import POSIX_ROOT, WINDOWS_ROOT, below, jobs_of, windows_path
from transcribe_ui.discovery.rules import posix_rules, windows_rules
from transcribe_ui.placement.classes import FolderLevel
from transcribe_ui.placement.levels import folder_levels


def test_levels_list_the_folders_below_the_scan_root() -> None:
    """Each level lists its first names (sorted, without case) and counts them; root files and outsiders add none."""
    sources = below(POSIX_ROOT, "b/X/e.mkv,A/Y/f.mkv,A/Y/g.mkv,C/h.mkv,D/i.mkv,root.mkv")
    jobs = jobs_of([*sources, "/elsewhere/Z/j.mkv", "relative/k.mkv"])
    expected = (
        FolderLevel(level=1, names=("A", "b", "C"), folders=4),
        FolderLevel(level=2, names=("X", "Y"), folders=2),
    )
    assert folder_levels(jobs, POSIX_ROOT, posix_rules()) == expected


def test_windows_levels_ignore_the_case_of_names() -> None:
    """Under the Windows rules `Season` and `season` are one folder; the first spelling is shown."""
    sources = [windows_path(path) for path in below(WINDOWS_ROOT, "Season/a.mkv,season/b.mkv")]
    levels = folder_levels(jobs_of(sources), windows_path(WINDOWS_ROOT), windows_rules())
    assert levels == (FolderLevel(level=1, names=("Season",), folders=1),)


def test_a_batch_without_folders_has_no_levels() -> None:
    """Files right in the scan root give no level to choose."""
    assert folder_levels(jobs_of(below(POSIX_ROOT, "a.mkv,b.mkv")), POSIX_ROOT, posix_rules()) == ()
