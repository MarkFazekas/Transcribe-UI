"""The `get_settings` and `update_settings` requests (no database needed)."""

from transcribe_contracts.constants import ALIGNMENT_LANGUAGES, WHISPER_LANGUAGES
from transcribe_ui.api.classes import (
    GetSettingsRequest,
    GetSettingsResponse,
    LanguageOption,
    Settings,
    UpdateSettingsRequest,
    UpdateSettingsResponse,
)
from transcribe_ui.api.ports import RequestContext


def language_options(settings: Settings) -> tuple[LanguageOption, ...]:
    """Return every Whisper language, sorted by code, with its switch and alignment availability."""
    enabled = frozenset(settings.enabled_languages)
    languages = sorted(WHISPER_LANGUAGES.items())
    return tuple(_option(code, name, enabled) for code, name in languages)


def _option(code: str, name: str, enabled: frozenset[str]) -> LanguageOption:
    alignment = code in ALIGNMENT_LANGUAGES
    return LanguageOption(code=code, name=name, enabled=code in enabled, alignment=alignment)


def get_settings(_request: GetSettingsRequest, context: RequestContext) -> GetSettingsResponse:
    """Return the stored settings (the defaults when none are stored)."""
    settings = context.ports.state.settings.load()
    return GetSettingsResponse(settings=settings, languages=language_options(settings))


def update_settings(request: UpdateSettingsRequest, context: RequestContext) -> UpdateSettingsResponse:
    """Store the request's settings, replacing the file atomically."""
    context.ports.state.settings.save(request.settings)
    return UpdateSettingsResponse(settings=request.settings, languages=language_options(request.settings))
