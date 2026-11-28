"""What the settings pre-fill on the New job page: the scan options and the languages offered.

Qt-free. `get_settings` is loaded at start-up and after every save (`settings_changed`). A
scan request takes its extensions and `include_hidden` from the settings, so the request is
complete on its own; until the settings answered (or when they cannot be read) it takes the
request's own defaults. The language choosers offer the enabled languages in the user's order,
plus any language a reopened job already uses.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from transcribe_ui.api.classes import ErrorResponse, GetSettingsRequest, GetSettingsResponse, Response, Settings
from transcribe_ui.discovery.classes import ScanSpec
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.selection.constants import SETTINGS_KEY
from transcribe_ui.i18n.catalog import tr

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


@dataclass(frozen=True)
class LanguageChoice:
    """A language a chooser offers."""

    code: str
    label: str


class SettingsSource:
    """The settings the page pre-fills from, loaded through `get_settings`."""

    def __init__(self, executor: Executor, notify: Callable[[], None]) -> None:
        """Load through `executor`; `notify` tells the page about a change."""
        self._channel = LatestChannel(executor)
        self._notify = notify
        self.settings: Settings | None = None
        # The error of the last load, or None.
        self.error: ErrorView | None = None
        # Whisper's name of each language, by code.
        self._names: Mapping[str, str] = MappingProxyType({})
        self._waiting: list[Proceed] = []

    def load(self, proceed: Proceed | None = None) -> None:
        """Load the settings; `proceed` (a start-up step's) is called once they answered."""
        if proceed is not None:
            self._waiting.append(proceed)
        self._channel.send(SETTINGS_KEY, GetSettingsRequest(action="get_settings"), self._loaded)

    def spec(self, root: str) -> ScanSpec:
        """Return the scan of `root` with the settings' options (the request's defaults without settings)."""
        settings = self.settings
        if settings is None:
            return ScanSpec(root=root)
        return ScanSpec(
            root=root,
            video_extensions=settings.video_extensions,
            audio_extensions=settings.audio_extensions,
            include_hidden=settings.include_hidden,
        )

    def languages(self, chosen: tuple[str, ...] = ()) -> tuple[LanguageChoice, ...]:
        """Return the languages to offer: the enabled ones in the user's order, then any other of `chosen`."""
        enabled = () if self.settings is None else self.settings.enabled_languages
        offered = [*enabled, *(code for code in chosen if code not in enabled)]
        return tuple(map(self._choice, offered))

    def _choice(self, code: str) -> LanguageChoice:
        name = self._names.get(code)
        if name is None:
            return LanguageChoice(code, code)
        label = tr("{name} ({code})").format(name=name.title(), code=code)
        return LanguageChoice(code, label)

    def _loaded(self, response: Response) -> None:
        if isinstance(response, GetSettingsResponse):
            self.settings = response.settings
            self._names = MappingProxyType({option.code: option.name for option in response.languages})
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self._notify()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()
