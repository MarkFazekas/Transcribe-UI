"""Other computers' jobs for the GUI tests (plan m6 M6.13): a job uploaded by the keyed installation, the list's view-model."""

from dataclasses import dataclass
from functools import partial

from support.batches import FIRST_BATCH_ID
from support.jobs import RecordingOutlet, RecordingRunner
from support.launch import launch, set_up_cloud
from support.runs import RunsWorld, seed_ready
from support.worker_sim import SimulatedJob, finished
from transcribe_ui.api.backend import Backend
from transcribe_ui.gui.download.others import OtherJobsModel
from transcribe_ui.gui.executor import SyncExecutor


def uploaded_elsewhere(world: RunsWorld, *, transcribed: bool) -> None:
    """Upload and publish the seeded batch on `world`; with `transcribed`, run it and let the worker finish every job."""
    ids = seed_ready(world)
    if not transcribed:
        return
    set_up_cloud(world)
    run_id = launch(world).run.run_id
    jobs = tuple(SimulatedJob(item_id, "en", "ok") for item_id in ids.values())
    finished(world.cloud, world.store, run_id, FIRST_BATCH_ID, jobs)


@dataclass
class OthersArea:
    """The list of other computers' jobs on `SyncExecutor`, with its outlet, its requests and its checks of the cloud."""

    model: OtherJobsModel
    outlet: RecordingOutlet
    runner: RecordingRunner
    # One entry per check of the cloud the list asked for (an adopted job's results).
    checks: list[None]


def others_area(backend: Backend) -> OthersArea:
    """Return the list's view-model over `backend`; its confirmations are accepted at once."""
    runner = RecordingRunner(backend)
    outlet = RecordingOutlet()
    checks: list[None] = []
    model = OtherJobsModel(SyncExecutor(runner), outlet, partial(checks.append, None))
    return OthersArea(model, outlet, runner, checks)
