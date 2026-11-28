"""The download dialog's view-model while choices change and loads are on their way (plan m6 M6.9).

The folder count and level ticks wait for the delay; a plan of choices changed since is cancelled
and ignored; a late settings answer keeps the user's choices; failures show the dialog every
time; the dialog's own `batches_changed` reloads it once; the export has its own Retry; a
plan stops when cancelled; a checked import runs on the short pool.
"""

import json
from functools import partial
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID
from support.download import FILM_ID, seed_download_batch, write_subtitles
from support.download_gui import DownloadArea, download_area, held_model
from support.runs import RunsWorld
from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import ErrorResponse, PlacementDefaults
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.gui.bridge import pool_for
from transcribe_ui.gui.constants import LONG_POOL, SHORT_POOL
from transcribe_ui.gui.download.constants import MAX_FOLDER_COUNT
from transcribe_ui.gui.download.form import form_from_settings
from transcribe_ui.placement.classes import ImportCustomPlanRequest, PlanDownloadRequest

# Output folder of the strategies that need one, below the leg's root.
OUT = "out"
# Request actions.
PLAN = "plan_download"
GET_BATCH = "get_batch"
# A folder count past what the dialog offers.
LARGE_COUNT = 500


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


@pytest.fixture
def area(world: RunsWorld) -> DownloadArea:
    """Return the download view-model of the seeded batch, opened."""
    created = download_area(world.leg)
    created.model.on_open()
    return created


def _actions(area: DownloadArea, start: int) -> list[str]:
    return [request.action for request in area.runner.requests[start:]]


def test_count_steps_wait_for_the_delay(area: DownloadArea) -> None:
    """Steps of the folder count plan nothing until the delay ends; then only the last count is planned."""
    area.model.edit(strategy="flat_with_folders", output_folder=area.folder(OUT))
    sent = len(area.runner.requests)
    area.model.edit(folder_count=2, soon=True)
    area.model.edit(folder_count=3, soon=True)
    waiting = area.shown()
    assert (_actions(area, sent), waiting.can_download, waiting.plan.loading) == ([], False, "Updating the plan…")
    area.deferral.fire()
    planned = area.runner.requests[sent:]
    assert [request.action for request in planned] == [PLAN]
    assert isinstance(planned[0], PlanDownloadRequest)
    assert (planned[0].placement.folder_count, area.shown().can_download) == (3, True)


def test_a_plan_of_changed_choices_is_ignored(world: RunsWorld) -> None:
    """A plan still on its way when the choices change is cancelled, and its answer changes nothing."""
    model, executor = held_model(world.leg)
    model.on_open()
    executor.release()
    executor.release()
    before = model.loaded
    model.edit(strategy="flat", output_folder=str(world.leg.root / OUT))
    in_flight = executor.held[-1]
    model.edit(strategy="mirror", output_folder="")
    executor.release()
    assert (in_flight.cancelled, model.loaded.error, model.loaded.loading) == (True, None, False)
    assert model.loaded.plan == before.plan


def test_late_settings_keep_the_users_choices(world: RunsWorld) -> None:
    """Choices made before the settings answered are not replaced by the pre-fill."""
    model, executor = held_model(world.leg)
    model.on_open()
    model.edit(strategy="flat", output_folder=str(world.leg.root / OUT))
    executor.release()
    assert (model.form.strategy, model.form.output_folder) == ("flat", str(world.leg.root / OUT))


def test_a_large_folder_count_is_clamped() -> None:
    """A settings count past what the dialog offers is shown as its largest count (it keeps every folder anyway)."""
    defaults = PlacementDefaults(
        strategy="flat_with_folders", output_folder=None, folder_count=LARGE_COUNT, overwrite=False
    )
    assert form_from_settings(defaults).folder_count == MAX_FOLDER_COUNT


def test_failures_show_the_dialog_every_time(world: RunsWorld, area: DownloadArea) -> None:
    """Each download that could not place a file asks to show the dialog, also when the failure repeats."""
    world.store.delete(result_srt_key(FIRST_BATCH_ID, FILM_ID, "en"))
    area.model.edit(strategy="mirror", output_folder=area.folder(OUT))
    assert area.model.download()
    assert area.outlet.shown_again == 1
    assert area.model.download()
    assert (area.outlet.shown_again, len(area.shown().run.failures)) == (2, 1)


def _dispatch(area: DownloadArea, signal_name: str, arguments: tuple[object, ...]) -> None:
    """Send `batches_changed` back to the dialog, as the window's bus does."""
    if signal_name == "batches_changed":
        area.model.on_batches_changed(arguments[0])


def test_the_own_change_reloads_once(area: DownloadArea) -> None:
    """The dialog's own `batches_changed`, sent back to it by the window, does not reload it a second time."""
    area.outlet.on_emit = partial(_dispatch, area)
    assert area.model.download()
    downloaded = _actions(area, 0).index("run_download") + 1
    assert _actions(area, downloaded) == [GET_BATCH, PLAN]


def test_an_export_error_has_its_own_retry(area: DownloadArea) -> None:
    """An export that cannot write shows with the custom plan, not the plan; Retry exports to the same file."""
    folder = area.leg.root / "plans"
    area.model.edit(strategy="custom", output_folder=area.folder("custom"))
    area.model.export.export(str(folder / "plan.json"))
    shown = area.shown()
    assert shown.export_error is not None
    assert (shown.export_error.code, shown.plan.error) == ("custom_plan_unwritable", None)
    folder.mkdir()
    area.model.export.retry()
    assert (area.model.export.path, area.shown().export_error) == (str(folder / "plan.json"), None)
    area.model.export.export(str(area.leg.root / "missing" / "plan.json"))
    area.model.edit(strategy="mirror")
    assert area.shown().export_error is None, "another strategy forgets the export's error"


def test_a_plan_stops_when_cancelled(world: RunsWorld, tmp_path: Path) -> None:
    """`plan_download` and a checked import stop at their next file system question once cancelled."""
    backend = world.leg.backend
    cancelled = CancelFlag()
    cancelled.cancel()
    plan = PlanDownloadRequest(action=PLAN, batch_id=FIRST_BATCH_ID)
    plan_file = tmp_path / "plan.json"
    export = {"action": "export_custom_plan", "batch_id": FIRST_BATCH_ID, "path": str(plan_file)}
    assert backend.execute_json(json.dumps(export)).status == "ok"
    check = ImportCustomPlanRequest.model_validate(
        {
            "action": "import_custom_plan",
            "batch_id": FIRST_BATCH_ID,
            "plan_path": str(plan_file),
            "placement": {"output_folder": str(tmp_path / "custom")},
            "check_only": True,
        },
    )
    for request in (plan, check):
        assert backend.execute(request, None, None).status == "ok"
        response = backend.execute(request, None, cancelled)
        assert isinstance(response, ErrorResponse)
        assert response.error.code == "cancelled"


def test_a_checked_import_runs_on_the_short_pool(tmp_path: Path) -> None:
    """Checking a custom plan writes nothing, so it never waits behind uploads; carrying it out is long."""
    fields = {
        "action": "import_custom_plan",
        "batch_id": FIRST_BATCH_ID,
        "plan_path": str(tmp_path / "plan.json"),
        "placement": {"output_folder": str(tmp_path)},
    }
    check = ImportCustomPlanRequest.model_validate({**fields, "check_only": True})
    carry = ImportCustomPlanRequest.model_validate(fields)
    assert (pool_for(check), pool_for(carry)) == (SHORT_POOL, LONG_POOL)
