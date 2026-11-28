"""Other computers' jobs in the cloud and their download here: the view-models (plan m6 M6.13; architecture §3.9).

Two installations share one bucket and one fake Runpod account: `runs_world` (with the Runpod
key) uploads the job and its run transcribes it; `r2_only_world` lists the cloud's jobs, adopts
the job through the backend's confirmation and downloads it, `mirror` pre-selected, into a
folder with the original relative structure. The requests of an adoption are a request golden
(`gui_others_*`), which the parity runner also sends through the backend and the CLI.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from support.batches import FILM, FIRST_BATCH_ID, PODCAST, build_batch_tree
from support.cloud import ManualExecutor, RecordingRunner, cloud_world
from support.credentials import moto_bucket
from support.download_gui import download_area
from support.golden import GOLDEN_DIR, assert_golden
from support.jobs import RecordingOutlet
from support.launch import FOREIGN_BATCH_ID
from support.launch_legs import LaunchLegs, seed_ready_leg
from support.others_gui import others_area, uploaded_elsewhere
from support.parity import run_parity
from support.runs import RunsWorld
from transcribe_ui.batches.cloud_responses import CloudBatch, ListCloudBatchesResponse
from transcribe_ui.gui.download.others import OtherJobsModel
from transcribe_ui.gui.download.others_display import CloudJobRow, cloud_job_row
from transcribe_ui.i18n.catalog import activate

SUBPROCESS_TIMEOUT_S = 60
# The Qt-free modules of the list of other computers' jobs.
QT_FREE_MODULES = ("transcribe_ui.gui.download.others", "transcribe_ui.gui.download.others_display")
# The requests of the list and the adoption.
LIST = "list_cloud_batches"
ADOPT = "adopt_cloud_batch"
# The bus signals the adoption emits.
BATCHES_CHANGED = "batches_changed"
OPEN_DOWNLOAD = "open_download"
# The audio length of a listed job: 1 h 2 min 5 s.
AUDIO_S = 3725


def test_the_view_models_do_not_load_qt() -> None:
    """The list's view-model imports no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_another_computers_job_is_adopted(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Listed, adopted through the confirmation; the jobs reload, its download opens, the cloud is checked."""
    uploaded_elsewhere(runs_world, transcribed=True)
    area = others_area(r2_only_world.leg.backend)
    model = area.model
    assert (model.listed, model.rows) == (False, ())
    model.look()
    assert [(row.batch_id, row.files, row.state) for row in model.rows] == [(FIRST_BATCH_ID, "2", "Transcribed")]
    model.download(FIRST_BATCH_ID)
    question = area.outlet.asked[0]
    expected = f"Add the cloud job {FIRST_BATCH_ID} (2 files), uploaded from another computer"
    assert question.confirmations[0].message.startswith(expected)
    sent = [(request.action, getattr(request, "confirm", None)) for request in area.runner.requests]
    emitted = [(BATCHES_CHANGED, ((FIRST_BATCH_ID,),)), (OPEN_DOWNLOAD, (FIRST_BATCH_ID,))]
    assert (area.outlet.emitted, sent) == (emitted, [(LIST, None), (ADOPT, False), (ADOPT, True), (LIST, None)])
    assert (area.checks, model.rows, model.adopting(FIRST_BATCH_ID)) == ([None], (), False)


def test_an_adopted_job_is_mirrored(runs_world: RunsWorld, r2_only_world: RunsWorld, tmp_path: Path) -> None:
    """Once its results are read, its download dialog has `mirror` pre-selected and keeps the original folders."""
    uploaded_elsewhere(runs_world, transcribed=True)
    area = others_area(r2_only_world.leg.backend)
    area.model.look()
    area.model.download(FIRST_BATCH_ID)
    # The page's check of the cloud reads the adopted job's results.
    assert r2_only_world.leg.backend.execute_json(json.dumps({"action": "refresh_status"})).status == "ok"
    dialog = download_area(r2_only_world.leg)
    dialog.model.on_open()
    assert (dialog.model.form.strategy, dialog.shown().plan.hint) == ("mirror", "Choose the output folder.")
    out = tmp_path / "mirror"
    dialog.model.edit(output_folder=str(out))
    assert (dialog.shown().can_download, dialog.model.download()) == (True, True)
    subtitles = (out / "Movies" / FILM.replace(".mkv", ".en.srt"), out / "Audio" / PODCAST.replace(".mp3", ".en.srt"))
    assert [path.is_file() for path in subtitles] == [True, True]


def test_a_job_adopted_meanwhile_is_refused(
    runs_world: RunsWorld,
    r2_only_world: RunsWorld,
) -> None:
    """A list shown before the job was adopted elsewhere in this app: Download shows `batch_exists`, the list reloads."""
    uploaded_elsewhere(runs_world, transcribed=False)
    stale = others_area(r2_only_world.leg.backend)
    stale.model.look()
    current = others_area(r2_only_world.leg.backend)
    current.model.look()
    current.model.download(FIRST_BATCH_ID)
    stale.model.download(FIRST_BATCH_ID)
    shown = [(error.code, error.message) for error in stale.outlet.errors]
    assert shown == [("batch_exists", f"A job with the id {FIRST_BATCH_ID} is already on this computer.")]
    assert (stale.outlet.asked, stale.outlet.emitted, stale.model.rows) == ([], [], ())


def test_a_declined_adoption_adds_nothing(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Cancel in the confirmation sends nothing more; the job stays listed."""
    uploaded_elsewhere(runs_world, transcribed=False)
    area = others_area(r2_only_world.leg.backend)
    area.outlet.accept = False
    area.model.look()
    area.model.download(FIRST_BATCH_ID)
    sent = [request.action for request in area.runner.requests]
    assert (sent, area.outlet.emitted, [row.batch_id for row in area.model.rows]) == (
        [LIST, ADOPT],
        [],
        [FIRST_BATCH_ID],
    )
    assert area.model.rows[0].state == "Waiting for transcription"


def _listed(*batches: CloudBatch) -> ListCloudBatchesResponse:
    return ListCloudBatchesResponse(batches=batches)


def _cloud_batch(batch_id: str, *, known: bool = False) -> CloudBatch:
    return CloudBatch(
        batch_id=batch_id,
        known=known,
        files=3,
        jobs=4,
        done_jobs=1,
        audio_s=AUDIO_S,
        state="transcribing",
    )


def test_only_the_latest_list_is_shown(tmp_path: Path) -> None:
    """A list answered after a newer one was sent is dropped; this computer's jobs are not listed."""
    with cloud_world(tmp_path) as world:
        manual = ManualExecutor(RecordingRunner(world.backend))
        model = OtherJobsModel(manual, RecordingOutlet(), lambda: None)
        model.look()
        model.look()
        manual.answer(LIST, _listed(_cloud_batch("batch_20260101_000001")))
        assert (model.rows, model.loading) == ((), True)
        manual.answer(LIST, _listed(_cloud_batch("batch_20260101_000002"), _cloud_batch(FIRST_BATCH_ID, known=True)))
        expected = CloudJobRow("batch_20260101_000002", "3", "1 h 2 min", "Transcribing: 1 of 4 done")
        assert (model.rows, model.loading) == ((expected,), False)


def test_the_states_in_hungarian() -> None:
    """The list's texts come from the catalog."""
    activate("hu")
    row = cloud_job_row(_cloud_batch(FOREIGN_BATCH_ID))
    assert (row.audio, row.state) == ("1 ó 2 p", "Átírás folyamatban: 1/4 kész")


def test_the_requests_of_an_adoption(tmp_path: Path) -> None:
    """Over the launch world (another computer's job in the cloud): the list, the question, the adoption, the list."""
    with cloud_world(tmp_path) as world:
        area = others_area(world.backend)
        area.model.look()
        assert [row.batch_id for row in area.model.rows] == [FOREIGN_BATCH_ID]
        area.model.download(FOREIGN_BATCH_ID)
        assert area.model.rows == ()
        documents = [request.model_dump(mode="json") for request in area.runner.requests]
    assert_golden("gui_others_adopt.json", f"{json.dumps(documents, indent=2, ensure_ascii=False)}\n")


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_others_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the list built gives the same response from the backend and the CLI."""
    templates = [json.dumps(document) for document in json.loads(golden.read_text(encoding="utf-8"))]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=LaunchLegs(seed=seed_ready_leg))
    statuses = [json.loads(exchange.response)["status"] for exchange in exchanges]
    assert statuses == ["ok", "needs_confirmation", "ok", "ok"]
