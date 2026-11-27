"""CLI/backend parity and golden files of the downloads and custom plans (plan m5 M5.13).

Each leg has its own moto bucket with the same subtitles and the same seeded `transcribed` batch:
the film transcribed (its English sidecar exists), the podcast without speech, the episode failed,
the silent film's subtitle extracted. Regenerate the golden files with `UPDATE_SNAPSHOTS=1`.
"""

import json
from pathlib import Path

from support.batches import FIRST_BATCH_ID, build_batch_tree
from support.credentials import moto_bucket
from support.download import (
    EXPORT_PLAN,
    IMPORT_PLAN,
    PLAN_DOWNLOAD,
    RUN_DOWNLOAD,
    placement,
    request,
    seed_download_batch,
    write_subtitles,
)
from support.golden import assert_golden
from support.launch_legs import LaunchLegs
from support.parity import run_parity
from support.runpod import Cloud
from transcribe_ui.api.ports import Ports
from transcribe_ui.storage.r2 import R2ObjectStore

# Names of the golden files, in request order.
STEPS = (
    "get",
    "plan",
    "overwrite_needs_confirmation",
    "next_to_media",
    "flat_with_folders",
    "export",
    "import",
    "missing_batch",
)


def _seed(ports: Ports, root: Path, store: R2ObjectStore, _cloud: Cloud) -> None:
    seed_download_batch(ports.state, root, "transcribed")
    write_subtitles(store)


def test_download_flow(tmp_path: Path) -> None:
    """Plan, a refused overwrite, two strategies, then a custom plan exported and imported unchanged."""
    custom = {"output_folder": "<root>/custom"}
    templates = [
        json.dumps({"action": "get_batch", "batch_id": FIRST_BATCH_ID}),
        request(PLAN_DOWNLOAD, placement=placement()),
        request(RUN_DOWNLOAD, placement=placement(overwrite=True)),
        request(RUN_DOWNLOAD, placement=placement()),
        request(RUN_DOWNLOAD, placement=placement("flat_with_folders", include_empty=True)),
        request(EXPORT_PLAN, path="<root>/plan.json"),
        request(IMPORT_PLAN, plan_path="<root>/plan.json", placement=custom),
        json.dumps({"action": PLAN_DOWNLOAD, "batch_id": "batch_20200101_000000"}),
    ]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=LaunchLegs(seed=_seed))
    for index, (name, exchange) in enumerate(zip(STEPS, exchanges, strict=True), start=1):
        assert_golden(f"download_flow_{index:02d}_{name}.json", exchange.golden())
