"""Probe edge cases of the review step: a file changing during its probe, an unreadable cache row (plan m5 M5.8)."""

import dataclasses
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import select

from support.batch_backend import REVIEW, probes_of, run_ok, start
from support.batches import FILM, FILM_PROBE, MEDIA_ROOT, batch_ports, build_batch_tree, fake_media
from support.parity import Leg
from transcribe_ui.api.ports import CancelToken, Ports
from transcribe_ui.batches.constants import PROBE_ATTEMPTS
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.models import ProbeCacheRow

# Target language of these tests.
HU = "hu"
# The film alone, as a selection path.
FILM_ONLY = (f"{MEDIA_ROOT}/Movies/{FILM}",)
# Bytes appended to a file to change its size and mtime during a probe.
GROWTH = b"more"


@dataclasses.dataclass
class GrowingMedia(FakeMediaTools):
    """Fake media tools whose first `growths` probes append to the file (it is still being written)."""

    growths: int = 0

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Probe, then grow the file while growths are left."""
        probed = super().probe(source, cancel)
        if self.growths:
            self.growths -= 1
            with source.open("ab") as appended:
                appended.write(GROWTH)
        return probed


def _growing_ports(media: GrowingMedia, root: Path) -> Ports:
    return dataclasses.replace(batch_ports(root), media=media)


def _cache_rows(leg: Leg, film: Path) -> list[tuple[int, int]]:
    with leg.ports.state.session() as session:
        rows = session.scalars(select(ProbeCacheRow).where(ProbeCacheRow.path == str(film)))
        return [(row.size_bytes, row.mtime_ns) for row in rows]


def _film(leg: Leg) -> Path:
    return leg.root / "media" / "Movies" / FILM


@pytest.mark.parametrize(
    ("growths", "probes", "problems"),
    [(1, 2, []), (PROBE_ATTEMPTS, PROBE_ATTEMPTS, ["source_changing"])],
)
def test_file_changing_during_its_probe(tmp_path: Path, growths: int, probes: int, problems: list[str]) -> None:
    """A file that changed during ffprobe is probed again; one that keeps changing is a problem and not cached."""
    media = GrowingMedia(probes=fake_media().probes, growths=growths)
    leg = Leg.create(tmp_path / "leg", None, build_batch_tree, partial(_growing_ports, media))
    with closing(leg):
        start(leg, HU, paths=FILM_ONLY)
        review = run_ok(leg, REVIEW)["review"]
        film = _film(leg)
        stat = film.stat()
        cached = _cache_rows(leg, film)
        probed = len(probes_of(leg))
    found = [entry["problem"]["code"] for entry in review["unresolved"] if entry["problem"] is not None]
    expected_cache = [] if problems else [(stat.st_size, stat.st_mtime_ns)]
    assert (probed, found, cached) == (probes, problems, expected_cache)


def test_unreadable_cache_row_is_a_miss(batch_leg: Leg) -> None:
    """A cache row in another payload shape is probed again and overwritten."""
    start(batch_leg, HU, paths=FILM_ONLY)
    film = _film(batch_leg)
    stat = film.stat()
    with batch_leg.ports.state.session() as session:
        stale = ProbeCacheRow(
            path=str(film),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            probed_at=batch_leg.ports.clock.now(),
            payload={"shape": "older"},
        )
        session.add(stale)
    review = run_ok(batch_leg, REVIEW)["review"]
    with batch_leg.ports.state.session() as session:
        payloads = list(session.scalars(select(ProbeCacheRow.payload).where(ProbeCacheRow.path == str(film))))
    assert (probes_of(batch_leg), review["complete"]) == ([f"probe {FILM}"], True)
    assert [ProbeResult.model_validate(payload) for payload in payloads] == [FILM_PROBE]
