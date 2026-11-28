"""The entries of the bundled ffmpeg, its zlib and Microsoft's runtime (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices_entries.py`: the FFmpeg entry (with the source it was built
from and the configure line of the build), the entry of the zlib that ffmpeg and ffprobe use (linked
statically or the OS's) and the entry of Microsoft's runtime libraries. Standard library only.
"""

from pathlib import Path

from build_desktop_licences import MICROSOFT_RUNTIME_LICENCE, PROJECT_URL, ZLIB_LICENCE
from build_desktop_notices_classes import BuildFacts, Draft, FfmpegBuild, Notice, make_notice

# The licence expression of FFmpeg as the build bundles it.
FFMPEG_EXPRESSION = "LGPL-2.1-or-later"
# The licence expression of zlib.
ZLIB_EXPRESSION = "Zlib"
# The name of the entry of the zlib that ffmpeg and ffprobe use.
ZLIB_ENTRY = "zlib (in ffmpeg and ffprobe)"
# The name of the entry of Microsoft's runtime libraries.
MICROSOFT_ENTRY = "Microsoft Visual C++ Runtime and Universal C Runtime"
# The licence expression of Microsoft's runtime libraries.
MICROSOFT_EXPRESSION = "LicenseRef-Microsoft-Redistributable"


def _text(mapping: object, key: str) -> str:
    """Return a text field of a JSON object ("" when it is missing or not a text)."""
    found = mapping.get(key) if isinstance(mapping, dict) else None
    return found if isinstance(found, str) else ""


def _first_url(source: object) -> str:
    urls = source.get("urls") if isinstance(source, dict) else None
    if not isinstance(urls, list) or not urls:
        return ""
    return urls[0] if isinstance(urls[0], str) else ""


def source_note(source: object) -> str:
    """Return where a pinned source comes from: the pin's first URL, its SHA-256 and the URL this build used."""
    note = f"Source: {_first_url(source)} (SHA-256 {_text(source, 'sha256')})."
    served = _text(source, "served_from")
    if served:
        note = f"{note} This build downloaded it from {served}."
    return note


def _ffmpeg_licence(ffmpeg: FfmpegBuild, source: object) -> tuple[Path, ...]:
    name, licence = _text(source, "name"), _text(source, "licence")
    if not name or not licence:
        return ()
    return (ffmpeg.folder / "licences" / f"{name}-{licence}",)


def _configure_line(ffmpeg: FfmpegBuild) -> str:
    configure = ffmpeg.description.get("configure")
    if not isinstance(configure, list):
        return ""
    return " ".join(map(str, configure))


def _zlib_notice(facts: BuildFacts, problems: list[str]) -> Notice:
    ffmpeg = facts.ffmpeg
    zlib = ffmpeg.description.get("zlib")
    if isinstance(zlib, dict):
        note = f"Linked statically into ffmpeg and ffprobe.\n{source_note(zlib)}"
        files = _ffmpeg_licence(ffmpeg, zlib)
        draft = Draft(ZLIB_ENTRY, _text(zlib, "version"), ZLIB_EXPRESSION, files, note)
        return make_notice(draft, problems)
    note = f"ffmpeg and ffprobe use {zlib}, which is part of the OS and not contained in this executable."
    files = (facts.licence_dir / ZLIB_LICENCE,)
    return make_notice(Draft(ZLIB_ENTRY, "the OS's", ZLIB_EXPRESSION, files, note), problems)


def ffmpeg_notices(facts: BuildFacts, problems: list[str]) -> list[Notice]:
    """Return the entries of the bundled ffmpeg and ffprobe: FFmpeg and the zlib they use."""
    ffmpeg = facts.ffmpeg
    source = ffmpeg.description.get("ffmpeg")
    note = (
        f"ffmpeg and ffprobe, built from the unmodified source by scripts/build_ffmpeg.py of {PROJECT_URL} "
        f"with: ./configure {_configure_line(ffmpeg)}\n{source_note(source)}\n"
        f"That exact source tarball is also published as an asset of the GitHub release that contains this "
        f"executable ({PROJECT_URL}/releases)."
    )
    files = _ffmpeg_licence(ffmpeg, source)
    draft = Draft("FFmpeg", _text(source, "version"), FFMPEG_EXPRESSION, files, note)
    return [make_notice(draft, problems), _zlib_notice(facts, problems)]


def microsoft_notice(facts: BuildFacts, problems: list[str]) -> Notice:
    """Return the entry of Microsoft's runtime libraries."""
    files = (facts.licence_dir / MICROSOFT_RUNTIME_LICENCE,)
    return make_notice(Draft(MICROSOFT_ENTRY, "as bundled", MICROSOFT_EXPRESSION, files), problems)
