"""The entries of the third-party notices (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`: from what the attribution found, the entry of each
distribution, of Python and the libraries inside it, of the bundled ffmpeg (with the source it
was built from), of Microsoft's runtime libraries and of the OS packages. A missing licence
expression or file is a problem, not an exception: every problem is collected, so the build
reports them all at once. Standard library only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from build_desktop_licences import (
    BUNDLED_LIBRARIES,
    LICENCE_OVERRIDES,
    MICROSOFT_RUNTIME_LICENCE,
    PROJECT_URL,
    PYTHON_EXPRESSION,
    ZLIB_LICENCE,
)
from build_desktop_notices_classes import (
    ENCODING,
    Attribution,
    BuildFacts,
    DistributionInfo,
    FfmpegBuild,
    Interpreter,
    LicenceText,
    Notice,
)

# OS packages by the names of their bundled files.
type Packages = Mapping[str, Sequence[str]]

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


@dataclass(frozen=True)
class Draft:
    """What an entry is made from: its name, version and expression, its licence files and its note."""

    name: str
    version: str
    expression: str
    licences: Sequence[Path]
    # Where it comes from, its source, which files it is (empty: nothing to add).
    note: str = ""


def read_licence(path: Path, problems: list[str]) -> LicenceText | None:
    """Return a licence file's text, or None (with a problem) when it cannot be read or is empty."""
    try:
        text = path.read_text(encoding=ENCODING, errors="replace")
    except OSError as error:
        problems.append(f"{path}: the licence file cannot be read: {error}")
        return None
    if not text.strip():
        problems.append(f"{path}: the licence file is empty")
        return None
    return LicenceText(path.name, text.strip("\n"))


def make_notice(draft: Draft, problems: list[str]) -> Notice:
    """Return an entry; a missing expression, no licence file or an unreadable one is a problem."""
    if not draft.expression.strip():
        problems.append(f"{draft.name}: no licence expression")
    if not draft.licences:
        problems.append(f"{draft.name}: no licence file")
    texts = [read_licence(path, problems) for path in draft.licences]
    found = tuple(text for text in texts if text is not None)
    return Notice(draft.name, draft.version, draft.expression, found, draft.note)


def distribution_notice(dist: DistributionInfo, licence_dir: Path, problems: list[str]) -> Notice:
    """Return the entry of a distribution, with its override (`LICENCE_OVERRIDES`) applied."""
    override = LICENCE_OVERRIDES.get(dist.key)
    expression = dist.expression if override is None else override.expression
    files = dist.licences
    if override is not None and override.licences:
        files = tuple(licence_dir / name for name in override.licences)
    return make_notice(Draft(dist.name, dist.version, expression, files), problems)


def python_notice(interpreter: Interpreter, problems: list[str]) -> Notice:
    """Return Python's entry."""
    files = () if interpreter.licence is None else (interpreter.licence,)
    minor = ".".join(interpreter.version.split(".")[:2])
    note = (
        "The interpreter is a python-build-standalone build (https://github.com/astral-sh/python-build-standalone). "
        "CPython contains further third-party code under the licences listed at "
        f"https://docs.python.org/{minor}/license.html; the libraries it links are listed as their own entries."
    )
    return make_notice(Draft("Python", interpreter.version, PYTHON_EXPRESSION, files, note), problems)


def library_notices(names: set[str], facts: BuildFacts, problems: list[str]) -> list[Notice]:
    """Return the entries of the interpreter's bundled libraries, in the table's order."""
    notices = []
    fallback = f"bundled with Python {facts.interpreter.version}"
    note = "Part of the Python interpreter in this executable."
    for library in BUNDLED_LIBRARIES:
        if library.name in names:
            version = facts.interpreter.library_versions.get(library.name, fallback)
            files = (facts.licence_dir / library.licence,)
            draft = Draft(library.name, version, library.expression, files, note)
            notices.append(make_notice(draft, problems))
    return notices


def package_notices(packages: Packages, facts: BuildFacts, problems: list[str]) -> list[Notice]:
    """Return the entries of the OS packages whose shared libraries the executable contains."""
    notices = []
    for name, bundled in sorted(packages.items()):
        package = None if facts.system is None else facts.system.describe(name)
        if package is None:
            problems.append(f"{name}: the OS package of {', '.join(bundled)} is not described by the package database")
            continue
        libraries = ", ".join(sorted(set(bundled)))
        note = f"Shared libraries of the build system's package {name}: {libraries}."
        draft = Draft(name, package.version, package.expression, package.licences, note)
        notices.append(make_notice(draft, problems))
    return notices


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
        f"with: ./configure {_configure_line(ffmpeg)}\n{source_note(source)}"
    )
    files = _ffmpeg_licence(ffmpeg, source)
    draft = Draft("FFmpeg", _text(source, "version"), FFMPEG_EXPRESSION, files, note)
    return [make_notice(draft, problems), _zlib_notice(facts, problems)]


def _microsoft_notice(facts: BuildFacts, problems: list[str]) -> Notice:
    files = (facts.licence_dir / MICROSOFT_RUNTIME_LICENCE,)
    return make_notice(Draft(MICROSOFT_ENTRY, "as bundled", MICROSOFT_EXPRESSION, files), problems)


def build_notices(found: Attribution, facts: BuildFacts) -> tuple[list[Notice], list[str]]:
    """Return every entry of the notices and the problems (the attribution's included)."""
    problems = list(found.problems)
    licence_dir = facts.licence_dir
    notices = [distribution_notice(dist, licence_dir, problems) for dist in found.distributions.values()]
    if facts.pyinstaller is None:
        problems.append("PyInstaller's distribution is not installed")
    elif facts.pyinstaller.key not in found.distributions:
        notices.append(distribution_notice(facts.pyinstaller, licence_dir, problems))
    if found.python or found.libraries:
        notices.append(python_notice(facts.interpreter, problems))
    notices.extend(library_notices(found.libraries, facts, problems))
    if found.ffmpeg:
        notices.extend(ffmpeg_notices(facts, problems))
    if found.microsoft:
        notices.append(_microsoft_notice(facts, problems))
    notices.extend(package_notices(found.packages, facts, problems))
    return notices, problems
