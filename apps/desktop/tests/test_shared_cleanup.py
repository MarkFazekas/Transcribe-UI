"""Deleting a job from the cloud when several computers download it (Q35, architecture §3.9 "Downloading twice").

The uploading installation (`runs_world`) gets the plain "delete everything" question; another
installation (`r2_only_world`, which adopted the job) gets the stronger warning, and so does a
manifest without `uploaded_by`. The installation id lives in the data dir.
"""

import json
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID
from support.cleanup import cleanup_document, confirmed
from support.cleanup import send as send_document
from support.launch import FOREIGN_BATCH_ID, foreign_manifest, launch, send, set_up_cloud, write_foreign
from support.runs import JSON_TYPE, RunsWorld, seed_ready
from support.worker_sim import SimulatedJob, finished
from transcribe_contracts.layout import manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.classes import NeedsConfirmation, Response
from transcribe_ui.state.installation import InstallationIdFile

# The plain question, and its stronger form.
PLAIN = "cleanup_everything"
ELSEWHERE = "cleanup_everything_uploaded_elsewhere"
# The same two forms for a job this computer does not know.
UNKNOWN_PLAIN = "cleanup_unknown_batch"
UNKNOWN_ELSEWHERE = "cleanup_unknown_batch_uploaded_elsewhere"
# The second question while the job's subtitles are not downloaded here.
NOT_DOWNLOADED = "cleanup_not_downloaded"
# An id the tests' id file is created with.
NEW_ID = "01JDZZZZZZZZZZZZZZZZZZZZZ7"


def _codes(response: Response) -> list[str]:
    assert isinstance(response, NeedsConfirmation), response
    return [confirmation.code for confirmation in response.confirmations]


def _asked(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> list[str]:
    return _codes(send_document(world, cleanup_document("everything", batch_id)))


def _downloaded_on_both(runs_world: RunsWorld, other: RunsWorld, tmp_path: Path) -> None:
    """Upload and transcribe on `runs_world`; download there, then adopt and download on `other`."""
    ids = seed_ready(runs_world)
    set_up_cloud(runs_world)
    run_id = launch(runs_world).run.run_id
    jobs = tuple(SimulatedJob(item_id, "en", "ok") for item_id in ids.values())
    finished(runs_world.cloud, runs_world.store, run_id, FIRST_BATCH_ID, jobs)
    for world in (runs_world, other):
        if world is other:
            send(world, "adopt_cloud_batch", batch_id=FIRST_BATCH_ID, confirm=True)
        send(world, "refresh_status")
        placement = {"strategy": "mirror", "output_folder": str(tmp_path / world.leg.root.name)}
        assert send(world, "run_download", batch_id=FIRST_BATCH_ID, placement=placement).status == "ok"


def test_uploading_computer_asks_plainly(runs_world: RunsWorld, r2_only_world: RunsWorld, tmp_path: Path) -> None:
    """Both computers downloaded: the uploader's question is plain, the other computer's carries the warning."""
    _downloaded_on_both(runs_world, r2_only_world, tmp_path)
    key = manifest_key(FIRST_BATCH_ID)
    body = runs_world.store.read(key)
    assert body is not None
    ids = (runs_world.ports.installation.current(), r2_only_world.ports.installation.current())
    assert (read_manifest(key, body).uploaded_by, ids[0] != ids[1]) == (ids[0], True)
    assert (_asked(runs_world), _asked(r2_only_world)) == ([PLAIN], [ELSEWHERE])


def test_repeated_cleanup_stays_plain(runs_world: RunsWorld, r2_only_world: RunsWorld, tmp_path: Path) -> None:
    """The database proves who uploaded it: repeating "everything" once the manifest is gone is still plain."""
    _downloaded_on_both(runs_world, r2_only_world, tmp_path)
    assert send_document(runs_world, confirmed("everything")).status == "ok"
    assert (runs_world.store.read(manifest_key(FIRST_BATCH_ID)), _asked(runs_world)) == (None, [PLAIN])


def test_manifest_without_installation_id_warns(runs_world: RunsWorld) -> None:
    """A manifest that does not say who uploaded it (1.0, or another app) gets the warning, unknown or adopted."""
    write_foreign(runs_world.store)
    unknown = _asked(runs_world, FOREIGN_BATCH_ID)
    send(runs_world, "adopt_cloud_batch", batch_id=FOREIGN_BATCH_ID, confirm=True)
    assert (unknown, _asked(runs_world, FOREIGN_BATCH_ID)) == ([UNKNOWN_ELSEWHERE], [ELSEWHERE, NOT_DOWNLOADED])


def test_unknown_job_of_this_installation_asks_plainly(runs_world: RunsWorld) -> None:
    """A job this database does not know, but this installation uploaded (a database deleted since): plain."""
    document = foreign_manifest().model_dump(mode="json") | {"uploaded_by": runs_world.ports.installation.current()}
    runs_world.store.write(manifest_key(FOREIGN_BATCH_ID), json.dumps(document).encode(), JSON_TYPE)
    assert _asked(runs_world, FOREIGN_BATCH_ID) == [UNKNOWN_PLAIN]


def test_installation_id_is_created_once_and_kept(tmp_path: Path) -> None:
    """Created on first use, read back by a later process; an invalid file is replaced by a new id."""
    path = tmp_path / "data" / "installation_id"
    created = InstallationIdFile(path, lambda: NEW_ID).current()
    kept = InstallationIdFile(path, lambda: "never used").current()
    path.write_text("not an id", encoding="ascii")
    replaced = InstallationIdFile(path, lambda: NEW_ID.replace("7", "8")).current()
    assert (created, kept, replaced, path.read_text(encoding="ascii")) == (
        NEW_ID,
        NEW_ID,
        NEW_ID.replace("7", "8"),
        replaced,
    )


def test_an_unreadable_installation_id_is_kept(tmp_path: Path) -> None:
    """A file that exists but cannot be read is an error; it is never replaced (the id is not lost)."""
    path = tmp_path / "installation_id"
    path.mkdir()
    with pytest.raises(OSError, match="installation_id"):
        InstallationIdFile(path, lambda: NEW_ID).current()
    assert path.is_dir()
