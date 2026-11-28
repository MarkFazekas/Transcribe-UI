"""Tiny media files generated with ffmpeg `lavfi` sources (architecture §7).

Every file is a couple of seconds long, so generating one takes a fraction of a second. ffmpeg
must be on `PATH` (CI installs it; tooling.md). Only FFmpeg's own encoders are used, so the
generator runs on the bundled LGPL build too (Opus through the native encoder, not libopus).
"""

import shutil
import subprocess
from pathlib import Path

# Two cues; the second has non-ASCII text, so the text encoding is checked too.
SRT_TEXTS = ("Hello world", "Árvíztűrő tükörfúrógép")
# The SRT those cues come from.
SOURCE_SRT = f"1\n00:00:00,000 --> 00:00:01,000\n{SRT_TEXTS[0]}\n\n2\n00:00:01,000 --> 00:00:02,000\n{SRT_TEXTS[1]}\n\n"
# Length of the generated video files.
VIDEO_SECONDS = 2
# Length of the generated audio files.
AUDIO_SECONDS = 1
# Longest ffmpeg may take to generate one file.
GENERATE_TIMEOUT_S = 60
# Pitch of the generated tones.
BASE_TONE_HZ = 440
# Pitch of a second audio track, so the two differ.
SECOND_TONE_HZ = 880
# lavfi video source of the generated videos (tiny frames, few of them).
VIDEO_SOURCE = f"testsrc=size=32x24:rate=5:duration={VIDEO_SECONDS}"
# The native Opus encoder of FFmpeg: experimental, and it takes 48 kHz input only.
OPUS = "opus"
# The only sample rate the native Opus encoder takes.
OPUS_RATE_HZ = "48000"
# A Matroska file whose English SubRip track (the two `SRT_TEXTS` cues) is zlib-compressed, which
# the Matroska muxer of FFmpeg cannot write. Made once with mkvmerge 82.0 from `SOURCE_SRT`:
# `mkvmerge --no-date --disable-track-statistics-tags --language 0:eng --compression 0:zlib
# -o zlib_subtitles.mkv source.srt`. FFmpeg reads the cues only when built with zlib.
ZLIB_SUBTITLES = Path(__file__).resolve().parent.parent / "fixtures" / "zlib_subtitles.mkv"
# The track's encoding in that file, as Matroska elements (ID, size, content): the encoding list
# (6 bytes) holding one content encoding (3 bytes) holding an empty content compression element,
# whose algorithm then is the default, 0: zlib.
ZLIB_ENCODING = bytes((0x6D, 0x80, 0x86, 0x62, 0x40, 0x83, 0x50, 0x34, 0x80))


def ffmpeg() -> str:
    """Return the ffmpeg on PATH.

    Raises:
        RuntimeError: ffmpeg is not installed.
    """
    found = shutil.which("ffmpeg")
    if found is None:
        msg = "ffmpeg is not on PATH; the media tests need it (see docs/ai/tooling.md)"
        raise RuntimeError(msg)
    return found


def generate(*arguments: str) -> None:
    """Run ffmpeg quietly with the arguments; fail loudly if it fails."""
    argv = [ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    subprocess.run(argv, check=True, capture_output=True, timeout=GENERATE_TIMEOUT_S)  # noqa: S603 - fixed argv


def lavfi(source: str) -> tuple[str, ...]:
    """Return the ffmpeg input arguments of a `lavfi` source."""
    return ("-f", "lavfi", *file_input(source))


def file_input(path: str) -> tuple[str, ...]:
    """Return the ffmpeg input arguments of a file."""
    return ("-i", path)


def encoder(name: str, stream: str = "a") -> tuple[str, ...]:
    """Return the output arguments that encode the stream specifier `stream` with an encoder.

    The native Opus encoder also gets `-strict experimental` and its 48 kHz sample rate.
    """
    arguments: tuple[str, ...] = (f"-c:{stream}", name)
    if name == OPUS:
        arguments += ("-strict", "experimental", f"-ar:{stream}", OPUS_RATE_HZ)
    return arguments


def sine(seconds: int, frequency: int = BASE_TONE_HZ) -> tuple[str, ...]:
    """Return the ffmpeg input arguments of a sine tone."""
    return lavfi(f"sine=frequency={frequency}:duration={seconds}")


def write_srt(folder: Path) -> Path:
    """Write the source SRT and return its path."""
    path = folder / "source.srt"
    path.write_text(SOURCE_SRT, encoding="utf-8")
    return path


def multitrack_video(folder: Path) -> Path:
    """Return a Matroska video with two tagged audio tracks and two text subtitle streams.

    Audio 0: AAC, `hun`, not default. Audio 1: Opus, `eng`, title "Original", default.
    Subtitle 0: SubRip, `eng`. Subtitle 1: ASS, `hun`, forced.
    """
    path = folder / "multitrack.mkv"
    srt = str(write_srt(folder))
    inputs = [*lavfi(VIDEO_SOURCE), *sine(VIDEO_SECONDS), *sine(VIDEO_SECONDS, SECOND_TONE_HZ)]
    inputs += [*file_input(srt), *file_input(srt)]
    mapping = ["-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map", "4:s"]
    codecs = ["-c:v", "mpeg4", *encoder("aac", "a:0"), *encoder(OPUS, "a:1"), "-c:s:0", "srt", "-c:s:1", "ass"]
    tags = ["-metadata:s:a:0", "language=hun", "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=Original"]
    tags += ["-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=hun"]
    flags = ["-disposition:a:0", "0", "-disposition:a:1", "default", "-disposition:s:0", "0"]
    flags += ["-disposition:s:1", "forced"]
    generate(*inputs, *mapping, *codecs, *tags, *flags, str(path))
    return path


def mov_text_video(folder: Path) -> Path:
    """Return an MP4 with an AAC track and a `mov_text` subtitle stream tagged `hun`."""
    path = folder / "subtitled.mp4"
    srt = str(write_srt(folder))
    generate(
        *sine(VIDEO_SECONDS),
        *file_input(srt),
        "-c:a",
        "aac",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=hun",
        str(path),
    )
    return path


def audio_file(folder: Path, encoder_name: str, extension: str) -> Path:
    """Return a one-track audio file made with the encoder."""
    path = folder / f"tone.{extension}"
    generate(*sine(AUDIO_SECONDS), *encoder(encoder_name), str(path))
    return path


def refused_codec_video(folder: Path) -> Path:
    """Return a QuickTime video whose audio codec (IMA ADPCM QT) the Matroska muxer refuses."""
    path = folder / "quicktime.mov"
    generate(*lavfi(VIDEO_SOURCE), *sine(VIDEO_SECONDS), "-c:v", "mpeg4", "-c:a", "adpcm_ima_qt", str(path))
    return path
