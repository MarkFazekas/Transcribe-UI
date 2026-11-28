"""The steps of the end-to-end flow and the simulated worker's changes between them (plan m5 M5.14, m6 M6.11).

The CLI flow (`test_end_to_end.py`) and the GUI flow (`test_gui_end_to_end.py`) share them: the
golden file of each step, the worker's outcome per job, and the changes the simulated worker
makes to a leg's cloud and bucket before the second and the third `refresh_status`.
"""

import json
from collections.abc import Callable, Mapping
from types import MappingProxyType

from support.batches import EPISODE, FILM, FIRST_BATCH_ID, PODCAST, item_ids
from support.golden import GOLDEN_DIR
from support.launch_legs import LaunchLegs
from support.parity import Exchange
from support.runpod import Cloud
from support.worker_sim import SimulatedJob, finished, launched_run_id, transcribing
from transcribe_contracts.classes import JobRef
from transcribe_ui.storage.r2 import R2ObjectStore

# Target languages of the batch.
EN = "en"
HU = "hu"
# The golden file names of the steps, in order.
STEPS = (
    "create",
    "start_review",
    "review",
    "upload",
    "plan",
    "launch",
    "refresh_started",
    "refresh_transcribing",
    "refresh_finished",
    "download",
    "retry",
    "plan_retry",
    "cleanup_audio_asks",
    "cleanup_audio",
    "cleanup_everything",
    "cleanup_retry_asks",
    "cleanup_retry",
    "list",
)
# Index of the `create_batch` step (its response lists the items), of the plan and of the launch.
CREATE_STEP = STEPS.index("create")
PLAN_STEP = STEPS.index("plan")
LAUNCH_STEP = STEPS.index("launch")

# Changes one leg's cloud and bucket, from the exchanges (or the GUI's answers) of the steps so far.
type LegChange = Callable[[Cloud, R2ObjectStore, list[Exchange]], None]


def golden_name(index: int) -> str:
    """Return the golden file name of the step at `index` (0-based)."""
    return f"end_to_end_{index + 1:02d}_{STEPS[index]}.json"


def golden_bytes(index: int) -> bytes:
    """Return the golden file of the step at `index`."""
    return (GOLDEN_DIR / golden_name(index)).read_bytes()


def step_ids(exchanges: list[Exchange]) -> dict[str, str]:
    """Return file name -> item id from the `create_batch` step."""
    return item_ids(exchanges[CREATE_STEP])


def outcomes(exchanges: list[Exchange]) -> tuple[SimulatedJob, ...]:
    """The worker's outcome per transcribe job: the episode fails in English, the podcast has no English speech."""
    ids = step_ids(exchanges)
    english = (
        SimulatedJob(ids[EPISODE], EN, "failed"),
        SimulatedJob(ids[PODCAST], EN, "no_speech"),
        SimulatedJob(ids[FILM], EN, "ok"),
    )
    hungarian = tuple(SimulatedJob(ids[name], HU, "ok") for name in (EPISODE, PODCAST, FILM))
    return english + hungarian


def worker_transcribing(cloud: Cloud, store: R2ObjectStore, exchanges: list[Exchange]) -> None:
    """The worker writes its heartbeat on the episode in English; the time passes."""
    run_id = launched_run_id(exchanges[LAUNCH_STEP])
    current = JobRef(batch_id=FIRST_BATCH_ID, item_id=step_ids(exchanges)[EPISODE], language=EN)
    transcribing(cloud, store, run_id, current)


def worker_finished(cloud: Cloud, store: R2ObjectStore, exchanges: list[Exchange]) -> None:
    """The worker writes every result, `_done.json`, its final heartbeat and its log; its pod ends."""
    run_id = launched_run_id(exchanges[LAUNCH_STEP])
    finished(cloud, store, run_id, FIRST_BATCH_ID, outcomes(exchanges))


# The worker's change before a step, by the step's name.
WORKER_CHANGES: Mapping[str, LegChange] = MappingProxyType(
    {"refresh_transcribing": worker_transcribing, "refresh_finished": worker_finished},
)


def change_legs(legs: LaunchLegs, change: LegChange, exchanges: list[Exchange]) -> None:
    """Make the change on every leg's cloud and bucket."""
    for cloud, store in zip(legs.clouds, legs.stores, strict=True):
        change(cloud, store, exchanges)


def retry_id(exchanges: list[Exchange]) -> str:
    """Return the retry batch's id from the `create_retry_batch` step."""
    batch_id = json.loads(exchanges[STEPS.index("retry")].response)["batch"]["batch_id"]
    assert isinstance(batch_id, str)
    return batch_id
