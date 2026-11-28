"""Whether this computer has a Runpod key (`credentials_status`; architecture §3.9 "Roles").

Qt-free. Without the key, Start transcription, Stop and Cancel and the cloud machines list are
shown as unavailable with the reason: another computer with a Runpod key starts the
transcription of every uploaded job, while this one uploads, checks for results and downloads.
Whether the key counts as set is the backend's `runpod_key_set` (a keyring that failed while
reading counts as holding it); a key that is not known yet (the status could not be read)
counts as present too, and the backend then answers with its own error. The Cloud page asks
again each time it is shown, so a key stored in Settings meanwhile takes effect there.
"""

from collections.abc import Callable

from transcribe_ui.api.classes import Response
from transcribe_ui.credentials.classes import CredentialsStatusRequest, CredentialsStatusResponse
from transcribe_ui.gui.cloud.constants import CREDENTIALS_KEY
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


def needs_key_hint() -> str:
    """Return the hint of an action that needs the Runpod key (a disabled button's tooltip, the machines list)."""
    return tr("Needs a Runpod API key on this computer.")


def missing_key_text() -> str:
    """Return the Cloud page's note while this computer has no Runpod key."""
    return tr(
        "This computer has no Runpod API key, so it cannot start or stop transcription or list the cloud "
        "machines. Another computer with a Runpod key starts the transcription of every uploaded job; this "
        "computer still uploads, checks for results and downloads the subtitles.",
    )


class RunpodKey(ViewModel):
    """Whether a Runpod key is set, as `credentials_status` answered last (None: not known yet)."""

    def __init__(self, executor: Executor) -> None:
        """Ask through `executor`."""
        super().__init__(executor)
        self._channel = ExclusiveChannel(executor)
        self.present: bool | None = None
        # Whether the R2 credentials are set, as the last answer said (None: not known yet).
        self.r2_present: bool | None = None
        # `proceed` of each start-up step waiting for the check in flight.
        self._waiting: list[Proceed] = []

    @property
    def missing(self) -> bool:
        """Return whether the last answer said that no Runpod key is set."""
        return self.present is False

    def check(self, proceed: Proceed | None = None) -> None:
        """Ask whether the key is set (a check in flight answers for both); `proceed` once answered.

        An error keeps the last answer.
        """
        if proceed is not None:
            # Before the send: an executor may answer inside it.
            self._waiting.append(proceed)
        if self._channel.busy(CREDENTIALS_KEY):
            return
        request = CredentialsStatusRequest(action="credentials_status")
        self._channel.send(CREDENTIALS_KEY, request, self._answered)

    def _answered(self, response: Response) -> None:
        if isinstance(response, CredentialsStatusResponse):
            # The rule of the backend: a keyring that failed while reading may hold the key.
            self.present = response.runpod_key_set
            self.r2_present = response.r2_set
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()
