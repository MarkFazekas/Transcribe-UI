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

from support.batches import EPISODE, FIRST_BATCH_ID, PODCAST, batch_request
from support.cleanup import AUDIO, EVERYTHING, cleanup_document, confirmed
from support.credentials import moto_bucket
from support.end_to_end import (
    EN,
    PLAN_STEP,
    STEPS,
    LegChange,
    change_legs,
    golden_name,
    retry_id,
    step_ids,
    worker_finished,
    worker_transcribing,
)
from support.golden import assert_golden
from support.launch import LAUNCH, PLAN, document
from support.launch_legs import LaunchLegs
from support.monitor import REFRESH
from support.parity import Exchange, Template, run_parity
from support.upload import REVIEWED, UPLOAD, build_upload_tree


def _launch(exchanges: list[Exchange]) -> str:
    fingerprint = json.loads(exchanges[PLAN_STEP].response)["plan"]["fingerprint"]
    return document(LAUNCH, confirm_fingerprint=fingerprint)


def _after(legs: LaunchLegs, change: LegChange, request: str) -> Template:
    return partial(_changed, legs, change, request)


def _changed(legs: LaunchLegs, change: LegChange, request: str, exchanges: list[Exchange]) -> str:
    change_legs(legs, change, exchanges)
    return request


def _retry(exchanges: list[Exchange]) -> str:
    ids = step_ids(exchanges)
    jobs = [{"item_id": ids[EPISODE], "language": EN}, {"item_id": ids[PODCAST], "language": EN}]
    return json.dumps({"action": "create_retry_batch", "batch_id": FIRST_BATCH_ID, "jobs": jobs})


def _on_retry(build: Callable[[str, str], str], scope: str) -> Template:
    """Return a template of a cleanup request on the retry batch (its id comes from the retry's response)."""
    return lambda exchanges: build(scope, retry_id(exchanges))


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
        _after(legs, worker_transcribing, document(REFRESH)),
        _after(legs, worker_finished, document(REFRESH)),
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
    assert phases == {FIRST_BATCH_ID: "cleaned", retry_id(exchanges): "cleaned"}
    assert len(exchanges) == len(STEPS)
    for index, exchange in enumerate(exchanges):
        assert_golden(golden_name(index), exchange.golden())
