"""The Cloud area's start-up steps (plan m6 §1 "Start-up sequence"; architecture §3.9 "Roles").

The run list loads in the slot `runs`. In the slot `reconcile`, `credentials_status` tells
whether a Runpod key is set; with one (or when that is not known), `reconcile_cloud` checks the
cloud machines. In the slot `refresh`, `refresh_status` checks the runs and the results with or
without the key (without it, the backend reads the results from R2 only). Each step proceeds
once its request answered, whatever the outcome.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.gui.cloud.area import CloudModels
from transcribe_ui.gui.signals import StartupHooks, StartupSlot

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


def _reconcile(models: CloudModels, proceed: Proceed) -> None:
    """Check the cloud machines, unless no Runpod key is set."""
    if models.key.missing:
        proceed()
    else:
        models.machines.check(proceed)


def _refresh(models: CloudModels, proceed: Proceed) -> None:
    """Check the runs and the results once (unless a check is in flight already)."""
    if not models.poll.tick(proceed):
        proceed()


def register_startup(hooks: StartupHooks, models: CloudModels) -> None:
    """Add the Cloud area's start-up steps: the run list, the key, the machines, the runs and results."""
    hooks.register(StartupSlot.RUNS, models.runs.load)
    hooks.register(StartupSlot.RECONCILE, models.key.check)
    hooks.register(StartupSlot.RECONCILE, partial(_reconcile, models))
    hooks.register(StartupSlot.REFRESH, partial(_refresh, models))
