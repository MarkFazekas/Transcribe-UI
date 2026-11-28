"""The review step on one backend: resume, probe cache, problems, batch ids, `batch_busy` (plan m5 M5.8).

The parity flow (`test_batches_flow.py`) records the responses; these tests look at what the
responses do not show: which files were probed, the probe cache, and concurrent requests.
"""

import dataclasses
import json
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from support.batch_backend import REVIEW, error_code, ids_of, probes_of, run, run_ok, start
from support.batches import (
    FILM,
    FIRST_BATCH_ID,
    MEDIA_ROOT,
    PODCAST,
    SECOND_BATCH_ID,
    SILENT,
    batch_ports,
    batch_request,
    build_batch_tree,
    composition,
    create_request,
    fake_media,
    transcribe,
)
from support.fakes import seeded_ports
from support.parity import Leg
from transcribe_ui.api.classes import ProgressEvent, Response
from transcribe_ui.api.ports import CancelFlag, CancelToken, Ports
from transcribe_ui.batches.phases import touch_batch
from transcribe_ui.discovery.rules import windows_rules
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.errors import PhaseConflictError
from transcribe_ui.state.models import BatchRow, ProbeCacheRow

# Target language of these tests.
HU = "hu"
# The second target language of the suggestions' test.
EN = "en"
# Longest a test waits for a thread to reach its blocking point or to finish.
THREAD_TIMEOUT_S = 10
# Content written over the film to change its size and mtime.
CHANGED = b"changed"
# The film alone, as a selection path.
FILM_ONLY = (f"{MEDIA_ROOT}/Movies/{FILM}",)
# A zone with daylight saving time: on 2026-10-25 its clocks go back from 03:00 (UTC+2) to 02:00 (UTC+1).
BUDAPEST = ZoneInfo("Europe/Budapest")
# 02:30 summer time (UTC+2), the first time the local clock shows 02:30 that night.
BEFORE_FALLBACK = datetime.fromisoformat("2026-10-25T00:30:00+00:00")
# 02:30 winter time (UTC+1), an hour later, the second time the local clock shows 02:30.
AFTER_FALLBACK = BEFORE_FALLBACK + timedelta(hours=1)


@dataclasses.dataclass
class CancelAfterFirst:
    """Progress sink that sets the cancel token at the first event (after the first probed item)."""

    token: CancelFlag

    def emit(self, _event: ProgressEvent) -> None:
        """Cancel."""
        self.token.cancel()


def test_interrupted_review_resumes_with_the_rest(batch_leg: Leg) -> None:
    """A cancelled review keeps the items probed so far; sending it again probes only the others."""
    start(batch_leg, HU)
    token = CancelFlag()
    cancelled = batch_leg.backend.execute_json(batch_leg.document(REVIEW), CancelAfterFirst(token), token)
    probed_first = len(probes_of(batch_leg))
    review = run_ok(batch_leg, REVIEW)["review"]
    probed = probes_of(batch_leg)
    every = len(ids_of(batch_leg))
    assert (error_code(cancelled), probed_first) == ("cancelled", 1)
    assert (review["items_in_review"], len(probed), len(set(probed))) == (every, every, every)


def test_probe_cache_serves_a_second_batch(batch_leg: Leg) -> None:
    """A file probed once is not probed again while its path, size and mtime are unchanged."""
    start(batch_leg, HU)
    run_ok(batch_leg, REVIEW)
    first = list(probes_of(batch_leg))
    run_ok(batch_leg, create_request(HU, confirm_existing_languages=True))
    run_ok(batch_leg, batch_request("start_review", SECOND_BATCH_ID))
    run_ok(batch_leg, batch_request("review_batch", SECOND_BATCH_ID))
    assert probes_of(batch_leg) == first


def test_changed_file_is_probed_again(batch_leg: Leg) -> None:
    """A file whose size changed misses the cache; its old cache entry is replaced."""
    start(batch_leg, HU, paths=FILM_ONLY)
    run_ok(batch_leg, REVIEW)
    film = batch_leg.root / "media" / "Movies" / FILM
    film.write_bytes(CHANGED)
    run_ok(batch_leg, create_request(HU, paths=FILM_ONLY, confirm_existing_languages=True))
    run_ok(batch_leg, batch_request("start_review", SECOND_BATCH_ID))
    run_ok(batch_leg, batch_request("review_batch", SECOND_BATCH_ID))
    with batch_leg.ports.state.session() as session:
        sizes = list(session.scalars(select(ProbeCacheRow.size_bytes).where(ProbeCacheRow.path == str(film))))
    assert probes_of(batch_leg) == [f"probe {FILM}", f"probe {FILM}"]
    assert sizes == [len(CHANGED)]


def test_missing_file_is_an_item_problem(batch_leg: Leg) -> None:
    """A file gone before the review is reported as a problem; the review is not complete."""
    start(batch_leg, HU, paths=(f"{MEDIA_ROOT}/Audio/{PODCAST}", *FILM_ONLY))
    (batch_leg.root / "media" / "Audio" / PODCAST).unlink()
    review = run_ok(batch_leg, REVIEW)["review"]
    problems = [(entry["name"], entry["problem"]["code"]) for entry in review["unresolved"]]
    assert (review["complete"], problems) == (False, [(PODCAST, "source_missing")])


def test_review_suggests_the_pre_selected_choices(batch_leg: Leg) -> None:
    """Each job of a probed file carries the pre-selection's track and its language's text subtitle, if any."""
    start(batch_leg, EN, HU)
    review = run_ok(batch_leg, REVIEW)["review"]
    files = {entry["name"]: entry for entry in review["subtitle_view"]}
    suggested = {
        name: [(entry["language"], entry["track_index"], entry["stream_index"]) for entry in files[name]["suggestions"]]
        for name in (FILM, SILENT)
    }
    # The film: English from its English (default) track or its English text subtitle, Hungarian from its
    # Hungarian track (its Hungarian subtitle is a picture); the silent video has no track to transcribe.
    assert suggested == {FILM: [(EN, 1, 0), (HU, 0, None)], SILENT: [(EN, None, None), (HU, None, 0)]}
    jobs = [[job["language"] for job in files[name]["jobs"]] for name in (FILM, SILENT)]
    assert jobs == [[EN, HU], [EN, HU]]


def test_edit_refused_after_composing(batch_leg: Leg) -> None:
    """Only a batch in `composing` can change its selection; start_review twice is harmless."""
    start(batch_leg, HU)
    before = ids_of(batch_leg)
    edit = {"action": "edit_batch", "batch_id": FIRST_BATCH_ID, **composition(HU)}
    refused = run(batch_leg, json.dumps(edit))
    again = run_ok(batch_leg, batch_request("start_review"))["batch"]
    assert (error_code(refused), again["phase"], ids_of(batch_leg)) == ("wrong_phase", "reviewing", before)


def _new_batch_id(leg: Leg, document: str) -> str:
    batch_id: str = run_ok(leg, document)["batch"]["summary"]["batch_id"]
    return batch_id


def test_batch_id_collision_and_time_zone(tmp_path: Path) -> None:
    """The id is the local creation time in the injected zone; a second batch in that second gets `_2`."""
    western = timezone(timedelta(hours=-5))
    factory = partial(seeded_ports, local_zone=western)
    leg = Leg.create(tmp_path / "leg", None, build_batch_tree, factory)
    with closing(leg):
        document = create_request(HU, confirm_existing_languages=True)
        found = [_new_batch_id(leg, document) for _attempt in range(3)]
    assert found == ["batch_20261127_070000", "batch_20261127_070000_2", "batch_20261127_070000_3"]


@dataclasses.dataclass
class SettableClock:
    """A clock the test moves between requests."""

    moment: datetime

    def now(self) -> datetime:
        """Return the current moment."""
        return self.moment


def _budapest_ports(clock: SettableClock, root: Path) -> Ports:
    return dataclasses.replace(seeded_ports(root, local_zone=BUDAPEST), clock=clock)


def test_batch_ids_across_the_autumn_fallback(tmp_path: Path) -> None:
    """The repeated local hour of the DST fallback gives the same base id, so the second batch gets `_2`."""
    clock = SettableClock(BEFORE_FALLBACK)
    leg = Leg.create(tmp_path / "leg", None, build_batch_tree, partial(_budapest_ports, clock))
    document = create_request(HU, confirm_existing_languages=True)
    with closing(leg):
        first = _new_batch_id(leg, document)
        clock.moment = AFTER_FALLBACK
        second = _new_batch_id(leg, document)
    assert (first, second) == ("batch_20261025_023000", "batch_20261025_023000_2")


def _windows_ports(root: Path) -> Ports:
    return dataclasses.replace(batch_ports(root), path_rules=windows_rules())


def test_item_languages_paths_follow_the_path_rule(tmp_path: Path) -> None:
    """On Windows an `item_languages` path matches whatever its case and separators."""
    leg = Leg.create(tmp_path / "leg", None, build_batch_tree, _windows_ports)
    spelled = f"{MEDIA_ROOT}\\AUDIO\\{PODCAST.upper()}"
    with closing(leg):
        detail = run_ok(leg, create_request(HU, item_languages=[{"path": spelled, "languages": ["en"]}]))["batch"]
    languages = {entry["name"]: [job["language"] for job in entry["jobs"]] for entry in detail["items"]}
    assert languages[PODCAST] == ["en"]


def _write_then_touch(leg: Leg) -> None:
    now = leg.ports.clock.now()
    with leg.ports.state.session() as session:
        session.add(ProbeCacheRow(path="x", size_bytes=1, mtime_ns=1, probed_at=now, payload={}))
        touch_batch(session, FIRST_BATCH_ID, ("composing",), now=now, also_set={"hidden": True})


def test_touch_batch_loser_changes_nothing(batch_leg: Leg) -> None:
    """A conditional change of a batch that left the expected phases is refused and rolled back."""
    start(batch_leg, HU)
    expected = {"batch_id": FIRST_BATCH_ID, "expected": "composing", "actual": "reviewing"}
    with pytest.raises(PhaseConflictError) as conflict:
        _write_then_touch(batch_leg)
    assert conflict.value.details == expected
    with batch_leg.ports.state.session() as session:
        cached = session.scalars(select(ProbeCacheRow)).first()
        row = session.get(BatchRow, FIRST_BATCH_ID)
        hidden = None if row is None else row.hidden
    assert (cached, hidden) == (None, False)


@dataclasses.dataclass
class BlockingMedia(FakeMediaTools):
    """Fake media tools whose probe waits until released, to hold a request inside its batch lock."""

    entered: threading.Event = dataclasses.field(default_factory=threading.Event)
    release: threading.Event = dataclasses.field(default_factory=threading.Event)

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Signal, wait for the release, then probe."""
        self.entered.set()
        assert self.release.wait(THREAD_TIMEOUT_S)
        return super().probe(source, cancel)


def _blocking_ports(media: BlockingMedia, root: Path) -> Ports:
    return dataclasses.replace(batch_ports(root), media=media)


def test_concurrent_requests_on_a_batch_get_batch_busy(tmp_path: Path) -> None:
    """While review_batch holds the batch lock, resolve_review, edit_batch and exclude_items get batch_busy."""
    media = BlockingMedia(probes=fake_media().probes)
    leg = Leg.create(tmp_path / "leg", None, build_batch_tree, partial(_blocking_ports, media))
    outcome: list[Response] = []
    with closing(leg):
        start(leg, HU)
        film = ids_of(leg)[FILM]
        worker = threading.Thread(target=lambda: outcome.append(run(leg, REVIEW)))
        worker.start()
        assert media.entered.wait(THREAD_TIMEOUT_S)
        busy = {
            error_code(run(leg, batch_request("resolve_review", choices=[transcribe(film, HU, 0)]))),
            error_code(run(leg, json.dumps({"action": "edit_batch", "batch_id": FIRST_BATCH_ID, **composition(HU)}))),
            error_code(run(leg, batch_request("exclude_items", confirm=True, item_ids=[film]))),
        }
        media.release.set()
        worker.join(THREAD_TIMEOUT_S)
    assert busy == {"batch_busy"}
    assert [response.status for response in outcome] == ["ok"]
