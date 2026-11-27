"""Pydantic models of the local state."""

from pydantic import BaseModel, ConfigDict

from transcribe_ui.api.classes import ToolPath


class ToolPaths(BaseModel):
    """The ffmpeg/ffprobe override of the settings file, read on its own.

    Other fields are ignored, so a problem elsewhere in the file never fails a tool call; a
    broken path field still does.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ffmpeg_path: ToolPath | None = None
    ffprobe_path: ToolPath | None = None
