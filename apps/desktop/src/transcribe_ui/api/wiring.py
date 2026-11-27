"""Composition root: builds the backend with its real adapters (tests inject fakes instead)."""

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports, SystemClock, UlidSource
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.tools import ToolLocator, bundle_dir
from transcribe_ui.state.paths import AppPaths
from transcribe_ui.state.store import LocalState


def build_backend() -> Backend:
    """Return the backend used by the frontends."""
    clock = SystemClock()
    # ffmpeg/ffprobe are looked up when first used.
    media = FfmpegMediaTools(ToolLocator(bundle_dir=bundle_dir()))
    state = LocalState(AppPaths.for_user())
    return Backend(Ports(clock=clock, ids=UlidSource(clock), media=media, state=state))
