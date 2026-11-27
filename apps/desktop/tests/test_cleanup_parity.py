"""CLI/backend parity and golden files of `create_retry_batch` and `cleanup_cloud` (plan m5 M5.14).

Each leg has its own moto bucket and fake Runpod API, seeded alike with the transcribed source
batch (the film failed, the podcast found no speech) and another computer's manifest. Regenerate
the golden files with `UPDATE_SNAPSHOTS=1`.
"""

import json
from pathlib import Path

from support.batches import build_batch_tree
from support.cleanup import FILM_ID, PODCAST_ID, cleanup_document, confirmed, retry_document, seed_transcribed
from support.credentials import moto_bucket
from support.golden import assert_golden
from support.launch import FOREIGN_BATCH_ID, write_foreign
from support.launch_legs import LaunchLegs
from support.parity import Template, run_parity
from support.runpod import Cloud
from transcribe_ui.api.ports import Ports
from transcribe_ui.storage.r2 import R2ObjectStore

# The golden file names of the steps, in order.
STEPS = (
    "retry_created",
    "retry_repeated",
    "audio_needs_confirmation",
    "audio_cleaned",
    "retry_refused_audio_deleted",
    "everything_needs_confirmations",
    "everything_cleaned",
    "unknown_needs_confirmation",
    "unknown_cleaned",
)


def _seed(ports: Ports, root: Path, store: R2ObjectStore, _cloud: Cloud) -> None:
    seed_transcribed(ports.state, root, store, podcast="no_speech")
    write_foreign(store)


def test_retry_and_cleanup_flow(tmp_path: Path) -> None:
    """Retry the film, clean up the source's audio, then everything, then another computer's batch."""
    templates: list[Template] = [
        retry_document(FILM_ID),
        retry_document(FILM_ID),
        cleanup_document("audio"),
        confirmed("audio"),
        retry_document(PODCAST_ID),
        cleanup_document("everything"),
        confirmed("everything"),
        cleanup_document("everything", FOREIGN_BATCH_ID),
        cleanup_document("everything", FOREIGN_BATCH_ID, confirm=True),
    ]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=LaunchLegs(seed=_seed))
    statuses = [json.loads(exchange.response)["status"] for exchange in exchanges]
    expected = ["ok", "ok", "needs_confirmation", "ok", "error", "needs_confirmation", "ok"]
    assert statuses == [*expected, "needs_confirmation", "ok"]
    for index, (name, exchange) in enumerate(zip(STEPS, exchanges, strict=True), start=1):
        assert_golden(f"cleanup_flow_{index:02d}_{name}.json", exchange.golden())
