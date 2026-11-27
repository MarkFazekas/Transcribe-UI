"""The end-to-end flow of the desktop backend (architecture §7, plan m5 M5.14), CLI and backend with goldens.

Create a batch from the scanned tree, review and upload it (the manifest is published), plan and
launch the worker, follow it while a simulated worker writes its heartbeat and then its results,
download and place the subtitles, retry the failed and no-speech jobs in a `careful` retry batch,
and clean up the cloud. Each leg has its own moto bucket and fake Runpod API, set up alike;
nothing talks to R2 or Runpod. Regenerate the golden files with `UPDATE_SNAPSHOTS=1`.
"""

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path

from support.batches import EPISODE, FILM, FIRST_BATCH_ID, PODCAST, batch_request, item_ids
from support.cleanup import AUDIO, EVERYTHING, cleanup_document, confirmed
from support.credentials import moto_bucket
from support.golden import assert_golden
from support.launch import LAUNCH, PLAN, document
from support.launch_legs import LaunchLegs
from support.monitor import REFRESH
from support.parity import Exchange, Template, run_parity
from support.upload import REVIEWED, UPLOAD, build_upload_tree
from support.worker_sim import SimulatedJob, finished, launched_run_id, transcribing
from transcribe_contracts.classes import JobRef

# Index of the `create_batch` exchange (its response lists the items) and of the launch.
CREATE_STEP = 0
PLAN_STEP = 4
LAUNCH_STEP = 5
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


def _ids(exchanges: list[Exchange]) -> dict[str, str]:
    return item_ids(exchanges[CREATE_STEP])


def _outcomes(exchanges: list[Exchange]) -> tuple[SimulatedJob, ...]:
    """The worker's outcome per transcribe job: the episode fails in English, the podcast has no English speech."""
    ids = _ids(exchanges)
    english = (
        SimulatedJob(ids[EPISODE], EN, "failed"),
        SimulatedJob(ids[PODCAST], EN, "no_speech"),
        SimulatedJob(ids[FILM], EN, "ok"),
    )
    hungarian = tuple(SimulatedJob(ids[name], HU, "ok") for name in (EPISODE, PODCAST, FILM))
    return english + hungarian


def _launch(exchanges: list[Exchange]) -> str:
    fingerprint = json.loads(exchanges[PLAN_STEP].response)["plan"]["fingerprint"]
    return document(LAUNCH, confirm_fingerprint=fingerprint)


# Changes both legs' clouds and buckets before a request, from the exchanges so far.
type LegsChange = Callable[[LaunchLegs, list[Exchange]], None]


def _after(legs: LaunchLegs, change: LegsChange, request: str) -> Template:
    return partial(_changed, legs, change, request)


def _changed(legs: LaunchLegs, change: LegsChange, request: str, exchanges: list[Exchange]) -> str:
    change(legs, exchanges)
    return request


def _transcribing(legs: LaunchLegs, exchanges: list[Exchange]) -> None:
    run_id = launched_run_id(exchanges[LAUNCH_STEP])
    current = JobRef(batch_id=FIRST_BATCH_ID, item_id=_ids(exchanges)[EPISODE], language=EN)
    for cloud, store in zip(legs.clouds, legs.stores, strict=True):
        transcribing(cloud, store, run_id, current)


def _finished(legs: LaunchLegs, exchanges: list[Exchange]) -> None:
    run_id = launched_run_id(exchanges[LAUNCH_STEP])
    for cloud, store in zip(legs.clouds, legs.stores, strict=True):
        finished(cloud, store, run_id, FIRST_BATCH_ID, _outcomes(exchanges))


def _retry(exchanges: list[Exchange]) -> str:
    ids = _ids(exchanges)
    jobs = [{"item_id": ids[EPISODE], "language": EN}, {"item_id": ids[PODCAST], "language": EN}]
    return json.dumps({"action": "create_retry_batch", "batch_id": FIRST_BATCH_ID, "jobs": jobs})


def _retry_id(exchanges: list[Exchange]) -> str:
    batch_id = json.loads(exchanges[STEPS.index("retry")].response)["batch"]["batch_id"]
    assert isinstance(batch_id, str)
    return batch_id


def _on_retry(build: Callable[[str, str], str], scope: str) -> Template:
    """Return a template of a cleanup request on the retry batch (its id comes from the retry's response)."""
    return lambda exchanges: build(scope, _retry_id(exchanges))


def _download() -> str:
    placement = {"strategy": "mirror", "output_folder": "<root>/out"}
    return batch_request("run_download", placement=placement)


def test_end_to_end_flow(tmp_path: Path) -> None:
    """From the scanned tree to placed subtitles, a careful retry and a clean cloud, the same on both legs."""
    legs = LaunchLegs()
    templates: list[Template] = [
        *REVIEWED,
        UPLOAD,
        document(PLAN),
        _launch,
        document(REFRESH),
        _after(legs, _transcribing, document(REFRESH)),
        _after(legs, _finished, document(REFRESH)),
        _download(),
        _retry,
        document(PLAN),
        cleanup_document(AUDIO),
        confirmed(AUDIO),
        cleanup_document(EVERYTHING, confirm=True),
        _on_retry(cleanup_document, EVERYTHING),
        _on_retry(confirmed, EVERYTHING),
        json.dumps({"action": "list_batches"}),
    ]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_upload_tree, ports=legs)
    documents = [json.loads(exchange.response) for exchange in exchanges]
    asked = {index for index, found in enumerate(documents) if found["status"] == "needs_confirmation"}
    assert [found for found in documents if found["status"] == "error"] == []
    assert asked == {STEPS.index("cleanup_audio_asks"), STEPS.index("cleanup_retry_asks")}
    phases = {batch["batch_id"]: batch["phase"] for batch in documents[-1]["batches"]}
    assert phases == {FIRST_BATCH_ID: "cleaned", _retry_id(exchanges): "cleaned"}
    for index, (name, exchange) in enumerate(zip(STEPS, exchanges, strict=True), start=1):
        assert_golden(f"end_to_end_{index:02d}_{name}.json", exchange.golden())
