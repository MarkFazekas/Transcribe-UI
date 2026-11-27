"""The manifest step interrupted: an R2 failure, a crash before the phase change, a misplaced runs lock (M5.9)."""

from typing import override

import pytest

from support.batches import FIRST_BATCH_ID
from support.credentials import TEST_BUCKET
from support.runs import HELD, READY, UPLOADING, RunsWorld, batch_state, read_back, seed_uploaded
from support.storage import store_client
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.storage.constants import ERROR_R2_UNREACHABLE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.publish import manifest_step


class Crash(Exception):
    """A simulated crash of the process."""


class UnwritableStore(R2ObjectStore):
    """The test bucket, but every write fails as if R2 could not be reached."""

    def __init__(self) -> None:
        """Use the test bucket."""
        super().__init__(store_client(), TEST_BUCKET)

    @override
    def write(self, key: str, body: bytes, content_type: str) -> None:
        raise StorageError(ERROR_R2_UNREACHABLE, "write", "EndpointConnectionError")


def _crash(*_args: object, **_kwargs: object) -> None:
    raise Crash


def test_a_failed_manifest_write_holds_the_manifest(runs_world: RunsWorld) -> None:
    """When the manifest cannot be written, the batch is held and the error raised; publishing resumes it."""
    seed_uploaded(runs_world)
    state = runs_world.ports.state
    with state.locks.batch(FIRST_BATCH_ID), runs_world.access() as cloud:
        broken = CloudAccess(UnwritableStore(), cloud.pods)
        with pytest.raises(StorageError):
            manifest_step(runs_world.context(), broken, FIRST_BATCH_ID)
    assert batch_state(runs_world) == HELD
    assert (runs_world.publish_held().published, batch_state(runs_world)) == ((FIRST_BATCH_ID,), READY)


def test_a_crash_after_the_write_is_resumed(runs_world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash between the manifest write and the phase change: the step again publishes the same bytes."""
    seed_uploaded(runs_world)
    with monkeypatch.context() as patched:
        patched.setattr("transcribe_ui.upload.publish._move_to_ready", _crash)
        with pytest.raises(Crash):
            runs_world.step()
    written = read_back(runs_world)
    assert batch_state(runs_world) == (*UPLOADING[:2], True)
    assert (runs_world.step().outcome, batch_state(runs_world), read_back(runs_world)) == ("published", READY, written)


def test_the_step_refuses_a_held_runs_lock(runs_world: RunsWorld) -> None:
    """Under the runs lock (launch, refresh), the step would wait for itself: a programming error."""
    seed_uploaded(runs_world)
    state = runs_world.ports.state
    with state.locks.runs(), runs_world.access() as cloud, pytest.raises(LockOrderError):
        manifest_step(runs_world.context(), cloud, FIRST_BATCH_ID)
    assert batch_state(runs_world) == UPLOADING
