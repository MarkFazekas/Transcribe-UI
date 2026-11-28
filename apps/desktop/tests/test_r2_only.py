"""Several installations on one bucket (architecture §3.9 "Roles", "Run scope"): an installation without the Runpod key.

Two backends share the moto bucket and the fake Runpod account: `runs_world` holds every
credential and starts the runs; `r2_only_world` holds only the R2 credentials. The R2-only one
uploads, checks for results, downloads and cleans up; the per-batch lease check replaces the
activity check for its cleanups.
"""

from support.batches import FIRST_BATCH_ID, PODCAST, SECOND_BATCH_ID, media_dir
from support.cleanup import confirmed
from support.cleanup import send as send_document
from support.launch import LAUNCH, error_of, launch, ok_as, plan_fingerprint, plan_of, send, set_up_cloud
from support.launch_seed import seed_audio_batch
from support.runs import READY, RunsWorld, batch_state, forget_runpod_key, seed_ready, seed_uploaded
from support.worker_sim import SimulatedJob, finished, leased_batches
from transcribe_ui.api.classes import Response
from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_MISSING
from transcribe_ui.runs.constants import ERROR_RUN_ACTIVE
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.upload.responses import RunUploadResponse

# Item of the second batch the R2-only installation uploads.
SECOND_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ31"


def _upload(world: RunsWorld, batch_id: str) -> None:
    """Finish the seeded upload through `run_upload` (R2 only): the manifest is published."""
    uploaded = ok_as(send(world, "run_upload", batch_id=batch_id), RunUploadResponse)
    assert (uploaded.outcome, batch_state(world, batch_id)) == ("ready", READY)


def _refresh(world: RunsWorld) -> RefreshStatusResponse:
    return ok_as(send(world, "refresh_status"), RefreshStatusResponse)


def test_r2_only_places_another_launch_results(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """The R2-only installation uploads; the other one starts the run; the results are placed next to the originals."""
    ids = seed_uploaded(r2_only_world)
    _upload(r2_only_world, FIRST_BATCH_ID)
    waiting = _refresh(r2_only_world)
    assert (waiting.awaiting_results, [batch.phase for batch in waiting.batches]) == (True, ["ready"])
    set_up_cloud(runs_world)
    known = [batch.known for batch in plan_of(runs_world).scope.batches]
    run = launch(runs_world).run
    assert (known, run.covered_batch_ids, leased_batches(runs_world.store, run.run_id)) == (
        [False],
        (FIRST_BATCH_ID,),
        (FIRST_BATCH_ID,),
    )
    jobs = tuple(SimulatedJob(item_id, "en", "ok") for item_id in ids.values())
    finished(runs_world.cloud, runs_world.store, run.run_id, FIRST_BATCH_ID, jobs)
    done = _refresh(r2_only_world)
    assert ([batch.phase for batch in done.batches], done.awaiting_results) == (["transcribed"], False)
    placed = send(r2_only_world, "run_download", batch_id=FIRST_BATCH_ID, placement={"strategy": "next_to_media"})
    assert placed.status == "ok", placed
    subtitle = media_dir(r2_only_world.leg.root) / "Audio" / PODCAST.replace(".mp3", ".en.srt")
    assert subtitle.is_file()


def _refused_by(response: Response) -> tuple[str, list[str]]:
    """Return the error code and the kinds of the activity signs that refused the request."""
    code, details = error_of(response)
    signs = details["sources"]
    assert isinstance(signs, list)
    return code, [str(sign["kind"]) for sign in signs if isinstance(sign, dict)]


def test_refresh_without_a_key_follows_no_run(runs_world: RunsWorld) -> None:
    """A run left open after the key was removed is not followed (`stop_error` says why); results are still read."""
    seed_ready(runs_world)
    set_up_cloud(runs_world)
    run_id = launch(runs_world).run.run_id
    forget_runpod_key(runs_world)
    refreshed = _refresh(runs_world)
    stopped = [(progress.run.run_id, progress.run.status, progress.stop_error) for progress in refreshed.runs]
    assert (stopped, refreshed.awaiting_results) == ([(run_id, "running", ERROR_CREDENTIALS_MISSING)], True)


def test_r2_only_cleanup_respects_the_run_scope(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """During a run the batch its lease lists cannot be cleaned up; one published after the launch can."""
    seed_uploaded(r2_only_world)
    _upload(r2_only_world, FIRST_BATCH_ID)
    set_up_cloud(runs_world)
    launch(runs_world)
    seed_audio_batch(r2_only_world, SECOND_BATCH_ID, SECOND_ITEM)
    _upload(r2_only_world, SECOND_BATCH_ID)
    refused = send_document(r2_only_world, confirmed("everything"))
    assert _refused_by(refused) == (ERROR_RUN_ACTIVE, ["lease"])
    allowed = send_document(r2_only_world, confirmed("everything", SECOND_BATCH_ID))
    assert (allowed.status, batch_state(r2_only_world, SECOND_BATCH_ID)) == ("ok", ("cleaned", False))


def test_second_keyed_launch_refused(runs_world: RunsWorld, keyed_world: RunsWorld) -> None:
    """Another installation with a Runpod key cannot start a second run: the live lease (and pod) refuse it."""
    seed_uploaded(runs_world)
    runs_world.step()
    set_up_cloud(runs_world)
    launch(runs_world)
    refused = send(keyed_world, LAUNCH, confirm_fingerprint=plan_fingerprint(keyed_world))
    code, kinds = _refused_by(refused)
    assert (code, "lease" in kinds) == (ERROR_RUN_ACTIVE, True)
