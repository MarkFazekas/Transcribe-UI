"""Display texts of the New job page (architecture §3.1 "GUI").

Qt-free, presentation only: the subtitles a file already has (from the scan's sidecars), the
media kind, sizes, the scan's state, the selection's totals (from the latest preview), the
warnings and why the job cannot be created yet. Nothing here decides what is selected.
"""

from typing import TYPE_CHECKING

from transcribe_contracts.classes import MediaKind
from transcribe_ui.discovery.classes import ScanCounts, ScanFile, ScanWarning, SelectionCounts, Sidecar
from transcribe_ui.gui.selection.constants import BYTES_PER_UNIT_STEP, LANGUAGE_SEPARATOR, ScanStatus
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.i18n.formats import format_number
from transcribe_ui.i18n.texts import code_text

if TYPE_CHECKING:
    from transcribe_ui.gui.selection.viewmodel import NewJobModel

# A warning as the page lists it: its text and its path.
type WarningRow = tuple[str, str]


def _languages(sidecars: tuple[Sidecar, ...], *, forced: bool) -> list[str]:
    """Return the distinct known languages of the forced (or not forced) sidecars, sorted."""
    found = {sidecar.language for sidecar in sidecars if sidecar.forced == forced}
    return sorted(language for language in found if language is not None)


def subtitles_text(scanned: ScanFile) -> str:
    """Return the subtitles a file already has: its languages, the forced ones marked, then the unknown ones."""
    sidecars = scanned.subtitles
    parts = _languages(sidecars, forced=False)
    for language in _languages(sidecars, forced=True):
        parts.append(tr("{language} (forced)").format(language=language))
    unknown = len([sidecar for sidecar in sidecars if sidecar.language is None])
    if unknown == 1:
        parts.append(tr("unknown language"))
    elif unknown:
        parts.append(tr("unknown language ({count})").format(count=unknown))
    return LANGUAGE_SEPARATOR.join(parts)


def subtitles_tooltip(scanned: ScanFile) -> str:
    """Return the names of a file's subtitle files, one per line."""
    return "\n".join(sidecar.name for sidecar in scanned.subtitles)


def kind_text(kind: MediaKind) -> str:
    """Return the name of a media kind."""
    return tr("Video") if kind == "video" else tr("Audio")


def size_text(size_bytes: int) -> str:
    """Return a size in decimal units (bytes, kB, MB, GB, TB) as the active language writes numbers."""
    units: tuple[str, ...] = (tr("{size} B"), tr("{size} kB"), tr("{size} MB"))
    units += (tr("{size} GB"), tr("{size} TB"))
    shown = float(size_bytes)
    step = 0
    last = len(units) - 1
    while shown >= BYTES_PER_UNIT_STEP and step < last:
        shown /= BYTES_PER_UNIT_STEP
        step += 1
    decimals = 1 if step else 0
    return units[step].format(size=format_number(shown, decimals))


def targets_text(languages: tuple[str, ...], *, own: bool) -> str:
    """Return the languages a selected file is transcribed into (its own ones marked)."""
    joined = LANGUAGE_SEPARATOR.join(languages)
    return tr("{languages} (this file)").format(languages=joined) if own else joined


def _found_text(counts: ScanCounts) -> str:
    total = counts.video_files + counts.audio_files
    if not total:
        return tr("No audio or video files in this folder.")
    text = ntr(
        "Found {count} media file ({video} video, {audio} audio).",
        "Found {count} media files ({video} video, {audio} audio).",
        total,
    )
    video = format_number(counts.video_files)
    audio = format_number(counts.audio_files)
    return text.format(count=format_number(total), video=video, audio=audio)


def _scanning_text(found: int) -> str:
    if not found:
        return tr("Scanning the folder…")
    text = ntr(
        "Scanning the folder… {count} media file found so far.",
        "Scanning the folder… {count} media files found so far.",
        found,
    )
    return text.format(count=format_number(found))


def scan_text(status: ScanStatus, found: int, counts: ScanCounts | None) -> str:
    """Return the line about the scan: what to do, its progress, its outcome (a failure shows its error instead)."""
    if status == "scanning":
        return _scanning_text(found)
    if status == "stopped":
        return tr("The scan was stopped; scan again any time.")
    if status == "ready" and counts is not None:
        return _found_text(counts)
    return tr("Choose a folder to list its audio and video files.") if status == "idle" else ""


def totals_text(counts: SelectionCounts | None) -> str:
    """Return the selection's totals from the latest preview ("" before it answered)."""
    if counts is None:
        return ""
    if not counts.files:
        return tr("Nothing is selected.")
    text = ntr(
        "{count} file selected ({video} video, {audio} audio), {size}.",
        "{count} files selected ({video} video, {audio} audio), {size}.",
        counts.files,
    )
    return text.format(
        count=format_number(counts.files),
        video=format_number(counts.video_files),
        audio=format_number(counts.audio_files),
        size=size_text(counts.size_bytes),
    )


def warning_rows(warnings: tuple[ScanWarning, ...]) -> tuple[WarningRow, ...]:
    """Return each warning as its text (from the catalog) and its path."""
    rows = []
    for warning in warnings:
        text = code_text("scan_warning", warning.code, {}, warning.message)
        rows.append((text, warning.path))
    return tuple(rows)


def save_hint(model: "NewJobModel") -> str | None:
    """Return why the job cannot be created (or saved) now, or None when it can."""
    counts = model.ticks.counts
    nothing = counts is not None and not counts.files
    if model.scan.scanned is None:
        return tr("Choose a folder, then tick the files to transcribe.")
    if nothing or not model.ticks.operations:
        return tr("Tick the files to transcribe.")
    if not model.targets.languages:
        return tr("Choose at least one language to transcribe into.")
    if model.saver.busy():
        return tr("Saving…")
    return None
