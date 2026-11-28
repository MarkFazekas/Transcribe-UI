"""The download dialog's view-model over the seeded `transcribed` batch (plan m6 M6.9).

Driven like a user on `SyncExecutor`: open the dialog, choose a strategy and its options,
download, confirm, cancel and resume, export and import a custom plan. The requests a scripted
state builds are request goldens (`gui_download_*`), which the parity runner also sends through
the backend and the CLI. The batch: the film transcribed (its English sidecar exists), the
podcast without speech, the episode failed, the silent film's subtitle extracted.
"""

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from support.batches import FIRST_BATCH_ID, build_batch_tree, media_dir
from support.credentials import moto_bucket
from support.download import FILM_ID, expected_outcomes, seed_download_batch, write_subtitles
from support.download_gui import DownloadArea, download_area
from support.golden import GOLDEN_DIR, assert_golden
from support.launch_legs import LaunchLegs
from support.monitor import SUBTITLE
from support.parity import run_parity
from support.runpod import Cloud
from support.runs import RunsWorld
from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import GetSettingsResponse, UpdateSettingsRequest
from transcribe_ui.api.ports import Ports
from transcribe_ui.gui.download.constants import StrategyChoice
from transcribe_ui.gui.texts import activity_state_text
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.placement.classes import PlacementOutcome
from transcribe_ui.storage.r2 import R2ObjectStore

if TYPE_CHECKING:
    from transcribe_ui.gui.download.form import FormChanges

SUBPROCESS_TIMEOUT_S = 60
# Strategy names of the tests.
MIRROR: Final[StrategyChoice] = "mirror"
FLAT_WITH_FOLDERS: Final[StrategyChoice] = "flat_with_folders"
# Outcome of a job whose subtitle was written.
PLACE: Final[PlacementOutcome] = "place"
# The phase of a batch whose every job has an outcome.
DOWNLOADED = "downloaded"
# Output folder of the strategies that need one, below the leg's root.
OUT = "out"
# The Qt-free modules of the download dialog.
QT_FREE_MODULES = (
    "transcribe_ui.gui.download.constants",
    "transcribe_ui.gui.download.display",
    "transcribe_ui.gui.download.form",
    "transcribe_ui.gui.download.running",
    "transcribe_ui.gui.download.state",
    "transcribe_ui.gui.download.viewmodel",
)
# The subtitle files each strategy writes below the leg's root (empty results left out).
WRITTEN: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "next_to_media": ("media/Movies/Silent.hu.srt",),
        "mirror": ("out/Movies/Film.en.srt", "out/Movies/Silent.hu.srt"),
        "flat": ("out/Film.en.srt", "out/Silent.hu.srt"),
        "flat_with_folders": ("out/Movies_Film.en.srt", "out/Movies_Silent.hu.srt"),
        "flat_with_levels": ("out/Movies_Film.en.srt", "out/Movies_Silent.hu.srt"),
    },
)


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


@pytest.fixture
def area(world: RunsWorld) -> DownloadArea:
    """Return the download view-model of the seeded batch, opened (pre-filled from the default settings)."""
    created = download_area(world.leg)
    created.model.on_open()
    return created


def _written(area: DownloadArea) -> tuple[str, ...]:
    root = area.leg.root
    found = [*root.glob("out/**/*.srt"), *root.glob("media/**/*.srt")]
    # The film's English sidecar was there before (an empty file the seed tree holds).
    return tuple(sorted(path.relative_to(root).as_posix() for path in found if path.read_bytes()))


def _outcomes(area: DownloadArea) -> dict[str, str]:
    outcome = area.model.run.outcome
    assert outcome is not None
    return {placement.item_id: placement.outcome for placement in outcome.placements}


def _phase(area: DownloadArea) -> str | None:
    summary = area.model.loaded.summary
    return None if summary is None else summary.phase


def _choose(area: DownloadArea, name: str) -> None:
    model = area.model
    out = area.folder(OUT)
    choices: dict[str, FormChanges] = {
        "next_to_media": {},
        MIRROR: {"strategy": MIRROR},
        "flat": {"strategy": "flat"},
        FLAT_WITH_FOLDERS: {"strategy": FLAT_WITH_FOLDERS},
        "flat_with_levels": {"strategy": FLAT_WITH_FOLDERS, "folder_mode": "levels"},
    }
    chosen = choices[name]
    if chosen:
        chosen["output_folder"] = out
    model.edit(**chosen)
    if name == "flat_with_levels":
        assert area.shown().plan.hint == "Choose at least one folder level."
        assert area.shown().levels == ((1, "Level 1: Audio, Movies"),)
        model.edit(folder_levels=frozenset((1,)))


def test_the_view_models_do_not_load_qt() -> None:
    """The download dialog's view-models import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("name", sorted(WRITTEN))
def test_every_strategy_downloads(area: DownloadArea, name: str) -> None:
    """Each strategy's choices make their plan, then their download; the batch is then downloaded."""
    _choose(area, name)
    shown = area.shown()
    assert (shown.plan.hint, shown.plan.errors, shown.can_download) == (None, (), True)
    assert area.model.download()
    film = "skip_existing" if name == "next_to_media" else PLACE
    assert (_outcomes(area), _written(area)) == (expected_outcomes(film=film), WRITTEN[name])
    assert (_phase(area), area.outlet.emitted[-1]) == (DOWNLOADED, ("batches_changed", ((FIRST_BATCH_ID,),)))
    assert_golden(f"gui_download_{name}.json", area.documents())


def test_the_plan_table_shows_outcomes_and_reasons(area: DownloadArea) -> None:
    """The table names each file, its target and what happens; the summary counts them."""
    area.model.edit(include_empty=True)
    shown = area.shown()
    rows = {row.file_name: (row.target_name, row.outcome_text) for row in shown.plan.rows}
    assert rows == {
        "Film.mkv": ("Film.en.srt", "Skipped: the file already exists"),
        "podcast.mp3": ("podcast.en.srt", "Will be placed"),
        "Episode.mkv": ("Episode.en.srt", "Skipped: the transcription failed"),
        "Silent.mkv": ("Silent.hu.srt", "Will be placed"),
    }
    expected = "2 subtitles will be placed, 0 files will be replaced, 2 skipped, 0 not finished yet"
    assert shown.plan.summary == expected


def test_the_choices_start_from_the_settings(world: RunsWorld) -> None:
    """The first opening pre-fills strategy, output folder, folder count and overwrite from the settings."""
    backend = world.leg.backend
    settings = backend.execute_json(json.dumps({"action": "get_settings"}))
    assert isinstance(settings, GetSettingsResponse)
    out = str(world.leg.root / OUT)
    placement = {"strategy": "flat_with_folders", "output_folder": out, "folder_count": 2, "overwrite": True}
    stored = {**settings.settings.model_dump(mode="json"), "placement": placement}
    update = UpdateSettingsRequest.model_validate({"action": "update_settings", "settings": stored})
    assert backend.execute(update, None, None).status == "ok"
    area = download_area(world.leg)
    area.model.on_open()
    form = area.model.form
    assert (form.strategy, form.output_folder, form.folder_count, form.overwrite) == tuple(placement.values())
    area.model.edit(strategy="flat")
    area.model.on_open()
    assert area.model.form.strategy == "flat", "a reopened dialog keeps the user's choices"
    assert_golden("gui_download_from_settings.json", area.documents(area.runner.requests[:3]))


def test_a_missing_output_folder_is_asked_for(area: DownloadArea) -> None:
    """A strategy that needs an output folder asks for one; nothing is planned or downloaded meanwhile."""
    sent = len(area.runner.requests)
    area.model.edit(strategy=MIRROR)
    shown = area.shown()
    assert (shown.plan.hint, shown.plan.rows, shown.can_download) == ("Choose the output folder.", (), False)
    assert (len(area.runner.requests), area.model.download()) == (sent, False)
    area.model.edit(output_folder="relative/out")
    assert area.shown().plan.errors == ("The output folder is not an absolute path.",)
    assert not area.shown().can_download


def _sidecar(area: DownloadArea) -> Path:
    return media_dir(area.leg.root) / "Movies" / "Film.en.srt"


def test_declined_overwrite_writes_nothing(area: DownloadArea) -> None:
    """Replacing the film's sidecar asks the backend's question first; Cancel writes nothing."""
    area.model.edit(overwrite=True)
    assert area.shown().plan.rows[0].outcome_text == "Will replace the existing file"
    area.outlet.accept = False
    assert area.model.download()
    asked = [view.confirmations[0].message for view in area.outlet.asked]
    assert asked == ["1 existing subtitle file will be replaced."]
    assert (_sidecar(area).read_bytes(), area.model.run.entry, area.activity.entries) == (b"", None, [])


def test_confirmed_overwrite_replaces(area: DownloadArea) -> None:
    """Confirm sends the same request again with `confirm_overwrite` set to the backend's `set_to`."""
    area.model.edit(overwrite=True)
    area.runner.requests.clear()
    assert area.model.download()
    assert (_sidecar(area).read_bytes(), _outcomes(area)) == (SUBTITLE, expected_outcomes(film="overwrite"))
    assert_golden("gui_download_overwrite.json", area.documents(area.runner.requests[:2]))


def test_resume_after_cancel(world: RunsWorld) -> None:
    """A cancelled download is stopped, not failed; Resume sends it again and it finishes the rest."""
    area = download_area(world.leg, cancel_after=1)
    area.model.on_open()
    _choose(area, MIRROR)
    area.model.edit(include_empty=True)
    assert area.model.download()
    stopped = area.shown().run
    shown_stopped = (stopped.status, stopped.can_resume, stopped.can_cancel, len(_written(area)), _phase(area))
    assert shown_stopped == (activity_state_text("stopped"), True, False, 1, "transcribed")
    area.model.run.resume()
    finished = area.shown().run
    assert (finished.status, finished.can_resume, finished.failures) == (activity_state_text("finished"), False, ())
    expected = "Last download: 2 written, 2 skipped, 0 not finished yet, 0 could not be placed."
    assert (finished.outcome, _phase(area)) == (expected, DOWNLOADED)
    downloads = [request for request in area.runner.requests if request.action == "run_download"]
    assert downloads[0] == downloads[1], "Resume sends the same request"


def test_a_failed_job_is_listed(world: RunsWorld) -> None:
    """A subtitle gone from the cloud is listed with its reason; the other jobs are placed."""
    area = download_area(world.leg)
    world.store.delete(result_srt_key(FIRST_BATCH_ID, FILM_ID, "en"))
    area.model.on_open()
    _choose(area, MIRROR)
    assert area.model.download()
    shown = area.shown().run
    assert [row.problem for row in shown.failures] == ["Film.mkv (en): the subtitle is not in the cloud any more."]
    assert shown.failures[0].target.endswith("Film.en.srt")


def _export(area: DownloadArea) -> Path:
    plan_file = area.leg.root / "plan.json"
    area.model.edit(strategy="custom", output_folder=area.folder("custom"))
    assert area.shown().plan.hint == "Export the plan, fill in each output folder and file name, then import it."
    area.model.export.export(str(plan_file))
    assert area.model.export.path == str(plan_file)
    return plan_file


def test_a_custom_plan_is_exported_checked_and_placed(area: DownloadArea) -> None:
    """Export writes the plan; importing it checks it first (`check_only`), then Download places it."""
    plan_file = _export(area)
    area.model.import_plan(str(plan_file))
    shown = area.shown()
    assert (shown.plan.errors, shown.can_download, shown.imported) == ((), True, f"Imported plan: {plan_file}")
    assert area.model.download()
    assert _outcomes(area) == expected_outcomes()
    written = sorted(path.relative_to(area.leg.root).as_posix() for path in area.leg.root.glob("custom/**/*.srt"))
    assert written == ["custom/Movies/Film.en.srt", "custom/Movies/Silent.hu.srt"]
    assert_golden("gui_download_custom.json", area.documents())


def test_import_problems_are_listed(area: DownloadArea) -> None:
    """Each problem of an edited plan is shown from the catalog; nothing can be downloaded until they are fixed."""
    plan_file = _export(area)
    document = json.loads(plan_file.read_text(encoding="utf-8"))
    entries = document["entries"]
    entries[0]["output_file_name"] = "Film.txt"
    entries[1]["output_file_name"] = entries[3]["output_file_name"]
    entries[1]["output_folder"] = entries[3]["output_folder"]
    plan_file.write_text(json.dumps(document), encoding="utf-8")
    area.model.import_plan(str(plan_file))
    shown = area.shown()
    assert shown.plan.errors == (
        "Film.mkv (en): output_file_name does not end with .srt.",
        "podcast.mp3 (en): another subtitle has the same target.",
        "Silent.mkv (hu): another subtitle has the same target.",
    )
    assert not shown.can_download
    assert not area.model.download()
    activate("hu")
    assert area.shown().plan.errors[0] == "Film.mkv (en): az output_file_name nem .srt végű."


def test_an_unreadable_plan_shows_its_error(area: DownloadArea) -> None:
    """A plan file that is not JSON lists the format problem; a missing one shows the error with Retry."""
    plan_file = area.leg.root / "broken.json"
    plan_file.write_text("{", encoding="utf-8")
    area.model.edit(strategy="custom", output_folder=area.folder("custom"))
    area.model.import_plan(str(plan_file))
    errors = area.shown().plan.errors
    assert len(errors) == 1
    assert errors[0].startswith("The custom plan does not match its format: ")
    area.model.import_plan(str(area.leg.root / "missing.json"))
    error = area.shown().plan.error
    assert error is not None
    assert (error.code, error.message) == (
        "custom_plan_unreadable",
        "The custom plan file cannot be read (FileNotFoundError).",
    )


def test_changes_elsewhere_reload_the_dialog(area: DownloadArea) -> None:
    """`batches_changed` naming the batch (or any batch) reloads it; another batch's change does not."""
    sent = len(area.runner.requests)
    area.model.on_batches_changed(("batch_20990101_000000",))
    assert len(area.runner.requests) == sent
    area.model.on_batches_changed(())
    area.model.on_batches_changed((FIRST_BATCH_ID,))
    reloaded = [request.action for request in area.runner.requests[sent:]]
    assert reloaded == ["get_batch", "plan_download", "get_batch", "plan_download"]


def _seed(ports: Ports, root: Path, store: R2ObjectStore, _cloud: Cloud) -> None:
    seed_download_batch(ports.state, root, "transcribed")
    write_subtitles(store)


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_download_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the download dialog built gives the same response from the backend and the CLI."""
    documents = json.loads(golden.read_text(encoding="utf-8"))
    templates = [json.dumps(document) for document in documents]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=LaunchLegs(seed=_seed))
    statuses = {json.loads(exchange.response)["status"] for exchange in exchanges}
    assert statuses <= {"ok", "needs_confirmation"}
