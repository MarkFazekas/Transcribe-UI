"""Composition root: builds the backend with its real adapters (tests inject fakes instead)."""

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports, SystemClock, UlidSource
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.tools import ToolLocator, bundle_dir


def build_backend() -> Backend:
    """Return the backend used by the frontends."""
    clock = SystemClock()
    # ffmpeg/ffprobe are looked up when first used; the path override from the settings file
    # joins `ToolLocator` once the settings exist (whichever of the two parts merges second).
    media = FfmpegMediaTools(ToolLocator(bundle_dir=bundle_dir()))
    return Backend(Ports(clock=clock, ids=UlidSource(clock), media=media))
