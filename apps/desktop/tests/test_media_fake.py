"""The fake media tools other parts' golden flows use: deterministic and shaped like the real ones."""

from pathlib import Path

import pytest

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.classes import AudioTrack, ProbeResult, SubtitleStream
from transcribe_ui.media.constants import (
    CONVERT_FAILED_MESSAGE,
    ERROR_MEDIA_INDEX_INVALID,
    ERROR_MEDIA_TOOL_FAILED,
    PROBE_FAILED_MESSAGE,
)
from transcribe_ui.media.fake import FakeMediaTools, default_fake_probe

# Duration of the registered file.
REGISTERED_DURATION_S = 5.0
# A registered probe result: two tracks (the second refused by Matroska), a text and a bitmap subtitle.
REGISTERED = ProbeResult(
    duration_s=REGISTERED_DURATION_S,
    start_s=0,
    has_video=True,
    audio_tracks=(
        AudioTrack(index=0, codec="ac3", language="hun", title=None, default=True, channels=6),
        AudioTrack(index=1, codec="adpcm_ima_qt", language="eng", title=None, default=False, channels=2),
    ),
    subtitle_streams=(
        SubtitleStream(index=0, codec="subrip", kind="text", language="eng", title=None, default=False, forced=False),
        SubtitleStream(
            index=1, codec="hdmv_pgs_subtitle", kind="bitmap", language="hun", title=None, default=False, forced=False
        ),
    ),
)


@pytest.fixture
def film(tmp_path: Path) -> Path:
    """Return an (empty) media file registered with the fake."""
    path = tmp_path / "film.mkv"
    path.write_bytes(b"")
    return path


@pytest.fixture
def fake() -> FakeMediaTools:
    """Return a fake with `film.mkv` registered."""
    return FakeMediaTools(probes={"film.mkv": REGISTERED})


def test_probe_by_name_or_default(tmp_path: Path, film: Path, fake: FakeMediaTools) -> None:
    """A registered name gives its result, any other file the default; every call is recorded."""
    other = tmp_path / "song.mp3"
    other.write_bytes(b"")

    assert (fake.probe(film, CancelFlag()), fake.probe(other, CancelFlag())) == (REGISTERED, default_fake_probe())
    assert fake.calls == ["probe film.mkv", "probe song.mp3"]


def test_convert_writes_a_deterministic_file(tmp_path: Path, film: Path, fake: FakeMediaTools) -> None:
    """The output names source and track; a refused codec goes to FLAC like the real adapter."""
    copied = fake.convert(film, 0, tmp_path / "out" / "item.a0", CancelFlag())
    encoded = fake.convert(film, 1, tmp_path / "out" / "item.a1", CancelFlag())

    assert (copied.path.name, copied.fell_back, encoded.path.name, encoded.fell_back) == (
        "item.a0.mka",
        False,
        "item.a1.flac",
        True,
    )
    assert copied.path.read_text(encoding="utf-8") == "fake mka audio of film.mkv, track 0 (ac3)\n"


def test_convert_of_a_missing_track(tmp_path: Path, film: Path, fake: FakeMediaTools) -> None:
    """A track the file does not have fails as the real adapter does."""
    with pytest.raises(DomainError) as caught:
        fake.convert(film, 2, tmp_path / "item", CancelFlag())

    assert (caught.value.code, caught.value.message) == (ERROR_MEDIA_TOOL_FAILED, CONVERT_FAILED_MESSAGE)


def test_extract_text_only(film: Path, fake: FakeMediaTools) -> None:
    """A text stream gives a one-cue SRT; a bitmap stream fails."""
    srt = fake.extract(film, 0, CancelFlag())

    assert srt == "1\n00:00:00,000 --> 00:00:01,000\nfilm.mkv subtitle 0 (eng)\n\n"
    with pytest.raises(DomainError):
        fake.extract(film, 1, CancelFlag())


def test_missing_file_and_cancel(tmp_path: Path, fake: FakeMediaTools) -> None:
    """A missing source is `media_tool_failed`; a set token is `cancelled` before anything is done."""
    with pytest.raises(DomainError) as caught:
        fake.probe(tmp_path / "gone.mkv", CancelFlag())
    flag = CancelFlag()
    flag.cancel()
    with pytest.raises(CancelledError):
        fake.probe(tmp_path / "gone.mkv", flag)

    assert (caught.value.code, caught.value.message) == (ERROR_MEDIA_TOOL_FAILED, PROBE_FAILED_MESSAGE)
    assert fake.calls == ["probe gone.mkv"]


def test_negative_index_refused(tmp_path: Path, film: Path, fake: FakeMediaTools) -> None:
    """A negative track or stream index is refused before anything is recorded or written."""
    with pytest.raises(DomainError) as convert_error:
        fake.convert(film, -1, tmp_path / "item", CancelFlag())
    with pytest.raises(DomainError) as extract_error:
        fake.extract(film, -1, CancelFlag())

    assert (convert_error.value.code, extract_error.value.code) == (
        ERROR_MEDIA_INDEX_INVALID,
        ERROR_MEDIA_INDEX_INVALID,
    )
    assert not fake.calls
