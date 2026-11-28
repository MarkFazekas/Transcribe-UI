"""The Cloud area's start-up steps (plan m6 §1 "Start-up sequence").

The run list loads in the slot `runs`. In the slot `reconcile`, `credentials_status`
tells whether a Runpod key is set; with one (or when that is not known), `reconcile_cloud`
checks the cloud machines, and in the slot `refresh` `refresh_status` checks the runs. Without
a key both checks are left out (M6.13 adds the screens without a key). Each step proceeds once
its request answered, whatever the outcome.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.api.classes import Response
from transcribe_ui.credentials.classes import CredentialsStatusRequest, CredentialsStatusResponse
from transcribe_ui.gui.cloud.area import CloudModels
from transcribe_ui.gui.cloud.constants import CREDENTIAL_MISSING, CREDENTIALS_KEY, RUNPOD_KEY_NAME
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.signals import StartupHooks, StartupSlot

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


class RunpodKey:
    """Whether a Runpod key is set, as `credentials_status` answered at start-up (None: not known)."""

    def __init__(self, executor: Executor) -> None:
        """Ask through `executor`."""
        self._channel = ExclusiveChannel(executor)
        self.present: bool | None = None

    def check(self, proceed: Proceed) -> None:
        """Ask whether the key is set; `proceed` once answered (an error leaves it unknown)."""
        request = CredentialsStatusRequest(action="credentials_status")
        answered = partial(self._answered, proceed)
        if self._channel.send(CREDENTIALS_KEY, request, answered) is None:
            proceed()

    def _answered(self, proceed: Proceed, response: Response) -> None:
        if isinstance(response, CredentialsStatusResponse):
            sources = {credential.name: credential.source for credential in response.credentials}
            source = sources.get(RUNPOD_KEY_NAME, CREDENTIAL_MISSING)
            self.present = source != CREDENTIAL_MISSING
        proceed()


def _reconcile(key: RunpodKey, models: CloudModels, proceed: Proceed) -> None:
    """Check the cloud machines, unless no Runpod key is set."""
    if key.present is False:
        proceed()
    else:
        models.machines.check(proceed)


def _refresh(key: RunpodKey, models: CloudModels, proceed: Proceed) -> None:
    """Check the runs once, unless no Runpod key is set (or a check is in flight already)."""
    if key.present is False or not models.poll.tick(proceed):
        proceed()


def register_startup(hooks: StartupHooks, models: CloudModels, key: RunpodKey) -> None:
    """Add the Cloud area's start-up steps: the run list, the key, the machines, the runs."""
    hooks.register(StartupSlot.RUNS, models.runs.load)
    hooks.register(StartupSlot.RECONCILE, key.check)
    hooks.register(StartupSlot.RECONCILE, partial(_reconcile, key, models))
    hooks.register(StartupSlot.REFRESH, partial(_refresh, key, models))
