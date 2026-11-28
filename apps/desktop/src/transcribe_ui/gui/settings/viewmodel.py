"""The settings page's preferences: load, edit, save with per-field errors (architecture §3.1 "GUI").

Qt-free. The form's state is a `SettingsDraft`; Save turns it into the whole `update_settings`
document. A document the request's validator refuses gets the backend's `invalid_request`
response (without a round trip), shown at the refused fields; a refused save never changes what
the user typed. After a save that changes the language of the texts, the window asks to
restart (Q32): the language is resolved once at start-up.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from transcribe_ui.api.classes import (
    ErrorResponse,
    GetSettingsRequest,
    GetSettingsResponse,
    LanguageOption,
    LanguageSetting,
    Request,
    ResetSettingsRequest,
    ResetSettingsResponse,
    Response,
    UpdateSettingsResponse,
)
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.feedback import error_message, error_view
from transcribe_ui.gui.settings.constants import LoadState
from transcribe_ui.gui.settings.draft import SettingsDraft, changed_fields, draft_of, settings_errors, update_request
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.restart import changes_language
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.constants import DEFAULT_LANGUAGE, Language

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


@dataclass
class PreferencesState:
    """What the preferences form shows."""

    state: LoadState = "loading"
    # Every Whisper language, sorted by code, with whether it is enabled and has word timing.
    languages: tuple[LanguageOption, ...] = ()
    # The stored settings as the form shows them, and the user's edits (None until loaded).
    saved: SettingsDraft | None = None
    draft: SettingsDraft | None = None
    # How to fix each field the last save refused, by field key.
    errors: dict[str, str] = field(default_factory=dict)
    # The error text of a failed load.
    load_error: str = ""
    message: str = ""
    saving: bool = False

    @property
    def can_save(self) -> bool:
        """Return whether Save applies: the form differs from the stored settings and no save runs."""
        edited = self.draft is not None and self.draft != self.saved
        return edited and not self.saving


def ready_state(saved: SettingsDraft, languages: tuple[LanguageOption, ...], message: str) -> PreferencesState:
    """Return the form's state for loaded or saved settings: no edits, no errors."""
    return PreferencesState(state="ready", languages=languages, saved=saved, draft=saved, message=message)


class PreferencesViewModel(ViewModel):
    """The editable settings (languages, file types, ffmpeg, placement defaults, language of the texts)."""

    def __init__(self, executor: Executor, hooks: AreaHooks, system_language: Language = DEFAULT_LANGUAGE) -> None:
        """Send through `executor`; report through `hooks`; `system_language`: what `system` shows."""
        super().__init__(executor)
        self._hooks = hooks
        self._system_language = system_language
        # The stored language when the save in flight was sent (None: nothing was loaded).
        self._language_before: LanguageSetting | None = None
        self.shown = PreferencesState()

    def load(self, proceed: Proceed | None = None) -> None:
        """Read the stored settings (`get_settings`); `proceed` (of a start-up step) is called when answered."""
        request = GetSettingsRequest(action="get_settings")
        self.executor.submit(request, partial(self._loaded, proceed))

    def edit(self, draft: SettingsDraft) -> None:
        """Take the form's new state; a field the user changed loses its error."""
        shown = self.shown
        if shown.draft is not None:
            for name in changed_fields(shown.draft, draft):
                shown.errors.pop(name, None)
        shown.draft = draft
        shown.message = ""
        self.changed()

    def revert(self) -> None:
        """Drop the edits: the form shows the stored settings again."""
        shown = self.shown
        shown.draft = shown.saved
        shown.errors = {}
        shown.message = ""
        self.changed()

    def save(self) -> None:
        """Save the whole form (`update_settings`); refused fields keep the user's text and show how to fix them."""
        draft = self.shown.draft
        if draft is None or self.shown.saving:
            return
        request = update_request(draft)
        if isinstance(request, ErrorResponse):
            self.shown.errors = settings_errors(request) or {}
            self.shown.message = tr("Fix the marked fields, then save again.")
            self.changed()
            return
        self._send(request)

    def restore_defaults(self) -> None:
        """Replace stored settings that cannot be read with the defaults (offered after a failed load)."""
        self._send(ResetSettingsRequest(action="reset_settings"))

    def _send(self, request: Request) -> None:
        saved = self.shown.saved
        self._language_before = None if saved is None else saved.language
        self.shown.saving = True
        self.shown.message = tr("Saving...")
        self.changed()
        self.executor.submit(request, self._saved)

    def _loaded(self, proceed: Proceed | None, response: Response) -> None:
        if isinstance(response, GetSettingsResponse):
            self.shown = ready_state(draft_of(response.settings), response.languages, "")
        elif isinstance(response, ErrorResponse) and self.shown.state != "ready":
            self.shown.state = "failed"
            self.shown.load_error = error_message(response.error)
        self.changed()
        if proceed is not None:
            proceed()

    def _saved(self, response: Response) -> None:
        if isinstance(response, UpdateSettingsResponse | ResetSettingsResponse):
            language = response.settings.language
            saved = draft_of(response.settings)
            self.shown = ready_state(saved, response.languages, tr("Saved."))
            self.changed()
            self._hooks.on_saved()
            # Asked once per change: a later save that keeps the language (after Later) does not ask again.
            if language != self._language_before and changes_language(language, self._system_language):
                self._hooks.on_language_changed()
        elif isinstance(response, ErrorResponse):
            self._refused(response)

    def _refused(self, response: ErrorResponse) -> None:
        refused = settings_errors(response)
        self.shown.saving = False
        self.shown.message = ""
        self.shown.errors = refused or {}
        self.changed()
        if refused is None:
            self._hooks.on_error(error_view(response))
