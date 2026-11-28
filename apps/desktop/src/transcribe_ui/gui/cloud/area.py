"""The view-models of the Cloud area, as one bundle the page, the dialog and the status bar bind to (Qt-free)."""

from dataclasses import dataclass

from transcribe_ui.gui.cloud.key import RunpodKey
from transcribe_ui.gui.cloud.machines import MachinesModel
from transcribe_ui.gui.cloud.poll import CloudPoll
from transcribe_ui.gui.cloud.ports import CloudOutlet, TimerFactory
from transcribe_ui.gui.cloud.run_actions import RunActions
from transcribe_ui.gui.cloud.runs import RunList
from transcribe_ui.gui.cloud.start import StartModel
from transcribe_ui.gui.executor import Executor


@dataclass(frozen=True)
class CloudModels:
    """The view-models of the Cloud page, its start dialog and the status bar line."""

    runs: RunList
    poll: CloudPoll
    actions: RunActions
    machines: MachinesModel
    start: StartModel
    # Whether a Runpod key is set: without one, start, stop and the machines list are unavailable.
    key: RunpodKey


def build_models(executor: Executor, outlet: CloudOutlet, timers: TimerFactory) -> CloudModels:
    """Return the Cloud area's view-models, wired to each other, sending through `executor`."""
    runs = RunList(executor, outlet)
    poll = CloudPoll(executor, outlet, runs, timers)
    actions = RunActions(executor, outlet, runs, poll)
    machines = MachinesModel(executor, outlet, runs.announce)
    start = StartModel(executor, outlet, actions.launched, runs.run_active)
    # Start follows whether a run is active.
    runs.subscribe(start.changed)
    return CloudModels(runs, poll, actions, machines, start, RunpodKey(executor))
