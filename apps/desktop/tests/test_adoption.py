"""Another computer's job (architecture §3.9 "Download of another installation's job").

`runs_world` uploads and starts the run; `r2_only_world`, another installation over the same
bucket, lists the cloud jobs, adopts the batch and downloads it from the relative paths. The
manifest is untrusted input: unsafe paths are refused at adoption, and the planner splits the
sources in the batch's own path flavour.
"""

import json
from pathlib import Path

import pytest

from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.launch import FOREIGN_BATCH_ID, error_code, foreign_manifest, launch, ok_as, send, set_up_cloud
from support.placement import FakeProbe, jobs_of, targets
from support.runs import JSON_TYPE, RunsWorld, seed_ready
from support.worker_sim import SimulatedJob, finished
from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.batches.cloud_responses import AdoptCloudBatchResponse, ListCloudBatchesResponse
from transcribe_ui.batches.responses import ListBatchesResponse
from transcribe_ui.discovery.rules import windows_rules
from transcribe_ui.placement.classes import PlacementOptions
from transcribe_ui.placement.naming import TargetNames
from transcribe_ui.placement.planner import plan_placement
from transcribe_ui.runs.responses import RefreshStatusResponse

# Key of `_hostile`'s changes that replaces the item's `original_path`.
PATH_KEY = "path"
# The request (and its confirmation code) of an adoption.
ADOPT = "adopt_cloud_batch"
# Where the mirror strategy places the adopted job's subtitles under Windows rules.
WINDOWS_OUTPUT = r"D:\subs"
# The other computer's scan root of the planner cases (POSIX).
POSIX_ROOT = "/media"


def _uploaded_and_transcribed(world: RunsWorld) -> dict[str, str]:
    """Publish the seeded batch, run it, and let the simulated worker finish every job; return name -> item id."""
    ids = seed_ready(world)
    set_up_cloud(world)
    run_id = launch(world).run.run_id
    jobs = tuple(SimulatedJob(item_id, "en", "ok") for item_id in ids.values())
    finished(world.cloud, world.store, run_id, FIRST_BATCH_ID, jobs)
    return ids


def _cloud_jobs(world: RunsWorld) -> list[tuple[str, bool, str, int, int, int]]:
    listed = ok_as(send(world, "list_cloud_batches"), ListCloudBatchesResponse)
    return [(job.batch_id, job.known, job.state, job.files, job.jobs, job.done_jobs) for job in listed.batches]


def _batch_ids(world: RunsWorld) -> list[str]:
    listed = ok_as(send(world, "list_batches", include_hidden=True), ListBatchesResponse)
    return [batch.batch_id for batch in listed.batches]


def _adopted(world: RunsWorld) -> AdoptCloudBatchResponse:
    """Ask for the adoption of the first batch, check the question, then confirm it."""
    asked = send(world, ADOPT, batch_id=FIRST_BATCH_ID)
    assert isinstance(asked, NeedsConfirmation)
    assert (asked.confirmations[0].code, _batch_ids(world)) == (ADOPT, [])
    return ok_as(send(world, ADOPT, batch_id=FIRST_BATCH_ID, confirm=True), AdoptCloudBatchResponse)


def test_another_computer_adopts_and_mirrors(runs_world: RunsWorld, r2_only_world: RunsWorld, tmp_path: Path) -> None:
    """The R2-only installation adopts the job and mirrors it into a folder with the original relative structure."""
    _uploaded_and_transcribed(runs_world)
    assert _cloud_jobs(r2_only_world) == [(FIRST_BATCH_ID, False, "transcribed", 2, 2, 2)]
    adopted = _adopted(r2_only_world)
    # The typed flag a frontend reads (the label is for display).
    assert (adopted.batch.adopted, "adopted" in adopted.batch.labels) == (True, True)
    refreshed = ok_as(send(r2_only_world, "refresh_status"), RefreshStatusResponse)
    phases = (adopted.batch.phase, [batch.phase for batch in refreshed.batches], _cloud_jobs(r2_only_world)[0][1])
    assert phases == ("ready", ["transcribed"], True)
    output = tmp_path / "mirror"
    placement = {"strategy": "mirror", "output_folder": str(output)}
    placed = send(r2_only_world, "run_download", batch_id=FIRST_BATCH_ID, placement=placement)
    subtitles = (
        output / "Movies" / FILM.replace(".mkv", ".en.srt"),
        output / "Audio" / PODCAST.replace(".mp3", ".en.srt"),
    )
    assert (placed.status, [path.is_file() for path in subtitles]) == ("ok", [True, True])


def test_adopted_batch_limits(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Next to the media, a retry and the audio cleanup are not possible for another computer's job."""
    ids = _uploaded_and_transcribed(runs_world)
    send(r2_only_world, ADOPT, batch_id=FIRST_BATCH_ID, confirm=True)
    send(r2_only_world, "refresh_status")
    planned = send(r2_only_world, "plan_download", batch_id=FIRST_BATCH_ID, placement={"strategy": "next_to_media"})
    plan_errors = [error["code"] for error in json.loads(planned.model_dump_json())["plan"]["errors"]]
    retry_jobs = [{"item_id": ids[FILM], "language": "en"}]
    retried = send(r2_only_world, "create_retry_batch", batch_id=FIRST_BATCH_ID, jobs=retry_jobs)
    cleaned = send(r2_only_world, "cleanup_cloud", batch_id=FIRST_BATCH_ID, scope="audio", confirm=True)
    assert (plan_errors, error_code(retried), error_code(cleaned)) == (
        ["no_local_sources"],
        "adopted_batch",
        "adopted_batch",
    )


def test_adopting_an_id_in_use_is_refused(runs_world: RunsWorld) -> None:
    """Ids are unique only per database: a job of an id this database has is refused, nothing changes."""
    seed_ready(runs_world)
    refused = send(runs_world, ADOPT, batch_id=FIRST_BATCH_ID, confirm=True)
    assert (error_code(refused), _batch_ids(runs_world)) == ("batch_exists", [FIRST_BATCH_ID])


def _hostile(world: RunsWorld, changes: dict[str, str]) -> None:
    """Publish the foreign manifest with top-level fields and its item's `original_path` (key `path`) replaced."""
    document = foreign_manifest().model_dump(mode="json")
    path = changes.pop(PATH_KEY, None)
    document.update(changes)
    if path is not None:
        document["items"][0]["original_path"] = path
    world.store.write(manifest_key(FOREIGN_BATCH_ID), json.dumps(document).encode(), JSON_TYPE)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({PATH_KEY: "/elsewhere/../etc/talk.flac"}, "manifest_unreadable"),
        ({PATH_KEY: "/other/talk.flac"}, "manifest_unreadable"),
        ({PATH_KEY: r"/elsewhere/..\..\x/talk.flac"}, "manifest_path_invalid"),
        ({PATH_KEY: "/elsewhere/C:evil/talk.flac"}, "manifest_path_invalid"),
        (
            {"path_flavor": "windows", "scan_root": r"D:\Media", PATH_KEY: r"D:\Media\C:x\talk.flac"},
            "manifest_path_invalid",
        ),
    ],
)
def test_hostile_manifest_is_refused(runs_world: RunsWorld, changes: dict[str, str], code: str) -> None:
    """`..` parts, a path outside the scan root, a drive or an anchor in a part: refused, nothing recorded."""
    _hostile(runs_world, changes)
    refused = send(runs_world, ADOPT, batch_id=FOREIGN_BATCH_ID, confirm=True)
    assert (error_code(refused), _batch_ids(runs_world)) == (code, [])


def _windows_plan(paths: list[str], strategy: str) -> list[str | None]:
    options = PlacementOptions.model_validate({"strategy": strategy, "output_folder": WINDOWS_OUTPUT})
    names = TargetNames(windows_rules(), "posix")
    plan = plan_placement(jobs_of(paths), POSIX_ROOT, options, names, FakeProbe())
    assert not plan.errors, plan.errors
    return targets(plan)


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("mirror", [r"D:\subs\.._x\movie.hu.srt", r"D:\subs\C_x\talk.hu.srt"]),
        ("flat_with_folders", [r"D:\subs\.._x_movie.hu.srt", r"D:\subs\C_x_talk.hu.srt"]),
    ],
)
def test_posix_parts_sanitised_under_windows_rules(strategy: str, expected: list[str]) -> None:
    r"""A POSIX batch's `..\x` and `C:x` folders are single names there; under Windows rules they are sanitised."""
    assert _windows_plan([r"/media/..\x/movie.mkv", "/media/C:x/talk.mkv"], strategy) == expected


def test_dot_dot_part_is_source_path_invalid() -> None:
    """A `..` part below the scan root is refused for any batch (`source_path_invalid`)."""
    options = PlacementOptions.model_validate({"strategy": "mirror", "output_folder": WINDOWS_OUTPUT})
    names = TargetNames(windows_rules(), "posix")
    plan = plan_placement(jobs_of(["/media/a/../movie.mkv"]), POSIX_ROOT, options, names, FakeProbe())
    assert [error.code for error in plan.errors] == ["source_path_invalid"]
