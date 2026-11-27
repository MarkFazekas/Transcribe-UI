"""The review step on the real ffprobe, with a generated multi-track video (plan m5 M5.8).

ffprobe's output differs across builds, so this asserts structure: which views list the file,
its tracks and streams, and the pre-selected choices (plan m5 §1 "Golden files").
"""

import dataclasses
from contextlib import closing
from pathlib import Path

from support import media
from support.batch_backend import REVIEW, run_ok
from support.batches import LOCAL_ZONE, MEDIA, MEDIA_ROOT, batch_request, create_request, extract
from support.fakes import seeded_ports
from support.parity import Leg
from transcribe_ui.api.ports import Ports
from transcribe_ui.media.adapter import FfmpegMediaTools


def _generate(root: Path) -> None:
    folder = root / MEDIA
    folder.mkdir()
    media.multitrack_video(folder)
    # The SRT the generator writes next to the video would be a sidecar of no media file.
    (folder / "source.srt").unlink()


def _ports(root: Path) -> Ports:
    return dataclasses.replace(seeded_ports(root, local_zone=LOCAL_ZONE), media=FfmpegMediaTools())


def test_review_with_real_ffprobe(tmp_path: Path) -> None:
    """The video is in both views; Hungarian takes the `hun` track, English the default `eng` one."""
    leg = Leg.create(tmp_path / "leg", None, _generate, _ports)
    with closing(leg):
        run_ok(leg, create_request("hu", "en", paths=(MEDIA_ROOT,)))
        run_ok(leg, batch_request("start_review"))
        review = run_ok(leg, REVIEW)["review"]
        in_tracks = review["track_view"]
        film = in_tracks[0]
        choice = extract(film["item_id"], "en", 0)
        resolved = run_ok(leg, batch_request("resolve_review", choices=[choice]))["review"]
    chosen = {job["language"]: (job["kind"], job["track_index"]) for job in film["jobs"]}
    assert (review["complete"], in_tracks, review["subtitle_view"]) == (True, [film], [film])
    assert [track["language"] for track in film["audio_tracks"]] == ["hun", "eng"]
    assert [stream["kind"] for stream in film["subtitle_streams"]] == ["text", "text"]
    assert chosen == {"hu": ("transcribe", 0), "en": ("transcribe", 1)}
    assert resolved["complete"] is True
