"""The settings form's state as the user edits it, and the settings document it becomes (Qt-free).

The form holds texts as typed (extensions as one line, paths possibly empty); `settings_document`
turns them into the `update_settings` document without deciding anything: the request's
validator (the backend's) accepts or refuses it.
"""

from dataclasses import dataclass, fields, replace

from pydantic import JsonValue

from transcribe_ui.api.classes import ErrorResponse, LanguageSetting, Request, Settings
from transcribe_ui.gui.settings.constants import (
    ERROR_FIELDS,
    EXTENSION_JOIN,
    EXTENSION_SEPARATORS,
    FORM_FIELD,
    SETTINGS_LOCATION,
)
from transcribe_ui.gui.settings.problems import field_errors, validated
from transcribe_ui.gui.settings.texts import settings_hint
from transcribe_ui.placement.classes import PlacementStrategy


@dataclass(frozen=True)
class SettingsDraft:
    """The settings as the form shows them; the attribute names are the problem keys of their fields."""

    # Enabled languages in the user's order: the stored order, a newly enabled one last.
    enabled_languages: tuple[str, ...]
    video_extensions: str
    audio_extensions: str
    include_hidden: bool
    # Empty: the bundled tool.
    ffmpeg_path: str
    ffprobe_path: str
    strategy: PlacementStrategy
    # Empty: none chosen yet.
    output_folder: str
    folder_count: int
    overwrite: bool
    language: LanguageSetting

    def with_language(self, code: str, *, enabled: bool) -> "SettingsDraft":
        """Return the draft with a language turned on (added last) or off."""
        kept = tuple(enabled_code for enabled_code in self.enabled_languages if enabled_code != code)
        return replace(self, enabled_languages=(*kept, code) if enabled else kept)


def draft_of(settings: Settings) -> SettingsDraft:
    """Return the form's state for stored settings."""
    placement = settings.placement
    return SettingsDraft(
        enabled_languages=settings.enabled_languages,
        video_extensions=EXTENSION_JOIN.join(settings.video_extensions),
        audio_extensions=EXTENSION_JOIN.join(settings.audio_extensions),
        include_hidden=settings.include_hidden,
        ffmpeg_path=settings.ffmpeg_path or "",
        ffprobe_path=settings.ffprobe_path or "",
        strategy=placement.strategy,
        output_folder=placement.output_folder or "",
        folder_count=placement.folder_count,
        overwrite=placement.overwrite,
        language=settings.language,
    )


def split_extensions(typed: str) -> list[JsonValue]:
    """Return the extensions typed into one field, in order (separated by spaces, commas or semicolons)."""
    return [extension for extension in EXTENSION_SEPARATORS.split(typed) if extension]


def optional_path(typed: str) -> str | None:
    """Return a typed path without surrounding white space; None when it is empty."""
    return typed.strip() or None


def settings_document(draft: SettingsDraft) -> dict[str, JsonValue]:
    """Return the `update_settings` document of the form's state (validated by the request's validator)."""
    settings: dict[str, JsonValue] = {
        "enabled_languages": list(draft.enabled_languages),
        "video_extensions": split_extensions(draft.video_extensions),
        "audio_extensions": split_extensions(draft.audio_extensions),
        "include_hidden": draft.include_hidden,
        "ffmpeg_path": optional_path(draft.ffmpeg_path),
        "ffprobe_path": optional_path(draft.ffprobe_path),
        "placement": {
            "strategy": draft.strategy,
            "output_folder": optional_path(draft.output_folder),
            "folder_count": draft.folder_count,
            "overwrite": draft.overwrite,
        },
        "language": draft.language,
    }
    return {"action": "update_settings", "settings": settings}


def update_request(draft: SettingsDraft) -> Request | ErrorResponse:
    """Return the `update_settings` request of the form's state, or the backend's `invalid_request` for it."""
    return validated(settings_document(draft))


def settings_errors(response: ErrorResponse) -> dict[str, str] | None:
    """Return the settings fields an `invalid_request` refused, with how to fix each; None for other errors.

    A field the form shows no error line for counts for the whole form (`FORM_FIELD`).
    """
    refused = field_errors(response, SETTINGS_LOCATION, settings_hint)
    if refused is None:
        return None
    errors: dict[str, str] = {}
    for field, hint in refused.items():
        errors.setdefault(field if field in ERROR_FIELDS else FORM_FIELD, hint)
    return errors


def changed_fields(before: SettingsDraft, after: SettingsDraft) -> set[str]:
    """Return the names of the fields whose value differs."""
    names = (field.name for field in fields(after))
    return {name for name in names if _differs(before, after, name)}


def _differs(before: SettingsDraft, after: SettingsDraft, name: str) -> bool:
    return bool(getattr(before, name) != getattr(after, name))
