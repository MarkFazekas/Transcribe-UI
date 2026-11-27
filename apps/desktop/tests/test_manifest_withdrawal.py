"""Withdrawing a leftover manifest before exclusion or re-review (plan m5 M5.9, architecture §3.3)."""

import threading

import pytest

from support.batch_backend import error_of, ids_of, item_phase
from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.runs import JSON_TYPE, MANIFEST, OUTAGE, RUN_ID, RunsWorld, app_pod_name, read_back
from support.withdrawal import (
    all_present,
    exclude_film,
    neighbour_untouched,
    none_present,
    pending_of,
    rereview,
    result_keys,
    seed_leftover,
)
from transcribe_contracts.classes import Manifest
from transcribe_contracts.layout import done_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.errors import Details, DomainError

# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10
# How long the simulated launch holds the runs lock: past the test backends' wait (0.5 s).
LONG_HOLD_S = 2.0
# Both seeded files.
BOTH = sorted((FILM, PODCAST))


def test_exclusion_withdraws_only_the_excluded_item(runs_world: RunsWorld) -> None:
    """Our manifest, `_done.json` and the excluded item's results go; the kept item's and the neighbour's stay."""
    ids = seed_leftover(runs_world)
    excluded = exclude_film(runs_world, ids)
    gone = none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *result_keys(FIRST_BATCH_ID, ids[FILM]))
    kept = all_present(runs_world, *result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    assert (excluded.status, getattr(excluded, "withdrawal_pending", None), gone, kept) == ("ok", False, True, True)
    assert (neighbour_untouched(runs_world, ids), pending_of(runs_world)) == (True, None)
    assert runs_world.step().outcome == "published"
    assert [entry.item_id for entry in read_manifest(MANIFEST, read_back(runs_world)).items] == [ids[PODCAST]]


def test_rereview_withdraws_only_that_item(runs_world: RunsWorld) -> None:
    """Re-review of the film: the manifest, `_done.json` and the film's results go; the podcast's stay."""
    ids = seed_leftover(runs_world)
    film = ids[FILM]
    still_pending = rereview(runs_world, film)
    gone = none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *result_keys(FIRST_BATCH_ID, film))
    kept = all_present(runs_world, *result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    assert (still_pending, gone, kept, neighbour_untouched(runs_world, ids), pending_of(runs_world)) == (
        False,
        True,
        True,
        True,
        None,
    )


def test_a_manifest_that_is_not_ours_is_left_alone(runs_world: RunsWorld) -> None:
    """Another computer's batch with the same id: its manifest and results are not ours to delete."""
    ids = seed_leftover(runs_world)
    runs_world.store.write(MANIFEST, b'{"another": "computer"}', JSON_TYPE)
    assert exclude_film(runs_world, ids).status == "ok"
    film_results = result_keys(FIRST_BATCH_ID, ids[FILM])
    assert all_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *film_results)


def test_an_older_manifest_of_ours_is_recognised(runs_world: RunsWorld) -> None:
    """Other bytes, but the batch's own `created_at` and `scan_root`: still ours, and withdrawn."""
    ids = seed_leftover(runs_world)
    ours = read_manifest(MANIFEST, read_back(runs_world))
    older = Manifest.model_validate(ours.model_dump() | {"items": ours.items[:1]})
    runs_world.store.write(MANIFEST, older.model_dump_json().encode(), JSON_TYPE)
    assert exclude_film(runs_world, ids).status == "ok"
    assert none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID))


def _refusal(world: RunsWorld, film: str, *, via_rereview: bool) -> tuple[str, Details]:
    """Exclude the film, or send it back to review; return the error code and details."""
    if not via_rereview:
        return error_of(exclude_film(world, {FILM: film}))
    try:
        rereview(world, film)
    except DomainError as error:
        return error.code, error.details
    pytest.fail("the re-review was not refused")


def _unchanged(world: RunsWorld, film: str) -> bool:
    batch_view = (sorted(ids_of(world.leg)), item_phase(world.leg, film))
    film_results = result_keys(FIRST_BATCH_ID, film)
    return batch_view == (BOTH, "uploaded") and all_present(world, MANIFEST, *film_results)


@pytest.mark.parametrize("via_rereview", [False, True])
def test_withdrawal_refused_while_a_run_is_active(runs_world: RunsWorld, *, via_rereview: bool) -> None:
    """A live pod: exclusion and re-review are refused with `run_active` naming the run; nothing changes."""
    film = seed_leftover(runs_world)[FILM]
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    code, details = _refusal(runs_world, film, via_rereview=via_rereview)
    assert (code, details.get("run_ids"), _unchanged(runs_world, film)) == ("run_active", [RUN_ID], True)


@pytest.mark.parametrize("via_rereview", [False, True])
def test_withdrawal_refused_when_the_cloud_is_unknown(runs_world: RunsWorld, *, via_rereview: bool) -> None:
    """The activity check failing is a refusal too (fail closed): nothing changes."""
    film = seed_leftover(runs_world)[FILM]
    runs_world.cloud.api.fail("GET", "/v2/pods", *OUTAGE)
    code, _details = _refusal(runs_world, film, via_rereview=via_rereview)
    assert (code, _unchanged(runs_world, film)) == ("cloud_unreachable", True)


def _hold_runs(world: RunsWorld, entered: threading.Event) -> None:
    with world.ports.state.locks.runs():
        entered.set()
        threading.Event().wait(LONG_HOLD_S)


def test_withdrawal_behind_a_launch_gives_runs_busy(runs_world: RunsWorld) -> None:
    """The guard waits for the runs lock only so long: `runs_busy`, and nothing changes."""
    ids = seed_leftover(runs_world)
    entered = threading.Event()
    launch = threading.Thread(target=_hold_runs, args=(runs_world, entered))
    launch.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    code = error_of(exclude_film(runs_world, ids))[0]
    launch.join(THREAD_TIMEOUT_S)
    assert (code, sorted(ids_of(runs_world.leg)), all_present(runs_world, MANIFEST)) == ("runs_busy", BOTH, True)
