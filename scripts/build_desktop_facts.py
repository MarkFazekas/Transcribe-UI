"""The facts of the build machine the third-party notices are made from (docs/ai/plans/m8.md, M8.3).

Runs inside the PyInstaller process of one variant (`apps/desktop/packaging/pyinstaller_main.py`),
whose interpreter and environment are the ones the executable bundles: the installed
distributions (their `RECORD` files and licence metadata), the interpreter (its prefix, licence,
built-in modules and the versions of the libraries it reports), the ffmpeg build's description
and, on Linux, the OS package database. `notices_entry` writes the notices and returns the entry
of the collected files that bundles them. Standard library only.
"""

import importlib
import json
import platform
import sys
import sysconfig
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from importlib.metadata import Distribution, PackagePath, distributions
from pathlib import Path
from types import MappingProxyType

from build_desktop_licences import PYINSTALLER
from build_desktop_notices import CollectedEntry, file_key, generate
from build_desktop_notices_classes import BuildFacts, DistributionInfo, FfmpegBuild, Interpreter, normalised
from build_desktop_packages import build_system_packages
from build_ffmpeg_steps import BUILD_INFO

# Reads the text of one library version from what the interpreter reports.
type Reader = Callable[[object], str | None]
# The distributions by the key of each file they installed, and by normalised name.
type DistributionIndex = tuple[dict[str, DistributionInfo], dict[str, DistributionInfo]]

# A sequence of folders.
type Folders = tuple[Path, ...]
# Longest `License` metadata field taken as an expression (longer ones are licence texts).
LICENSE_FIELD_LIMIT = 120
# Prefix of a licence classifier (its last part names the licence).
LICENCE_CLASSIFIER = "License :: "
# Names of licence files a distribution without `License-File` metadata may have in its metadata folder.
LICENCE_NAMES = ("LICEN", "COPYING", "NOTICE")
# Name of the notices file inside the app's package (`transcribe_ui.constants.THIRD_PARTY_NOTICES_FILE`).
NOTICES_FILE = "THIRD_PARTY_NOTICES.txt"
# The app's top-level package; the notices are bundled as its data.
PACKAGE_NAME = "transcribe_ui"
# The name of the metadata folder of an installed distribution (its suffix).
METADATA_SUFFIX = ".dist-info"
# Separator of the classifier's parts.
CLASSIFIER_SEPARATOR = " :: "
# Name of Python's licence file.
PYTHON_LICENCE_FILE = "LICENSE.txt"
# This folder, which the PyInstaller runner puts on its module search path after the analysis.
SCRIPTS_DIR = Path(__file__).resolve().parent


def _text_of(metadata: Mapping[str, object], key: str) -> str:
    """Return a metadata field as a stripped text ("" when it is missing or no text)."""
    found = metadata.get(key)
    return found.strip() if isinstance(found, str) else ""


def _located(dist: Distribution, path: PackagePath) -> Path:
    return Path(str(dist.locate_file(path)))


def _expression(metadata: Mapping[str, object], classifiers: list[str]) -> str:
    """Return the licence expression: `License-Expression`, else a short `License`, else the licence classifiers."""
    expression = _text_of(metadata, "License-Expression")
    if expression:
        return expression
    field = _text_of(metadata, "License")
    if field and "\n" not in field and len(field) <= LICENSE_FIELD_LIMIT:
        return field
    licences = [name for name in classifiers if name.startswith(LICENCE_CLASSIFIER)]
    parts = (name.rsplit(CLASSIFIER_SEPARATOR, 1) for name in licences)
    return ", ".join(part[-1] for part in parts)


def _in_metadata_folder(path: PackagePath) -> bool:
    return bool(path.parts) and path.parts[0].endswith(METADATA_SUFFIX)


def _inner(path: PackagePath) -> str:
    """Return a file's path inside its metadata folder."""
    return "/".join(path.parts[1:])


def _declared(dist: Distribution, files: list[PackagePath]) -> list[Path]:
    """Return the files of the metadata folder that the `License-File` fields name."""
    found: list[Path] = []
    for name in dist.metadata.get_all("License-File") or []:
        wanted = name.replace("\\", "/")
        accepted = {wanted, f"licenses/{wanted}"}
        matches = [path for path in files if _inner(path) in accepted]
        first = matches[:1]
        found.extend(_located(dist, path) for path in first)
    return found


def licence_paths(dist: Distribution) -> tuple[Path, ...]:
    """Return a distribution's licence files: those `License-File` names, else licence-like files of its metadata folder."""
    files = [path for path in dist.files or () if _in_metadata_folder(path)]
    if dist.metadata.get_all("License-File"):
        return tuple(_declared(dist, files))
    like = [path for path in files if path.name.upper().startswith(LICENCE_NAMES)]
    return tuple(_located(dist, path) for path in like)


def describe_distribution(dist: Distribution) -> DistributionInfo:
    """Return a distribution's name, version, licence expression and licence files."""
    metadata = dist.metadata
    classifiers = metadata.get_all("Classifier") or []
    fields = {key: metadata.get(key) for key in ("License-Expression", "License")}
    expression = _expression(fields, classifiers)
    return DistributionInfo(metadata["Name"], dist.version, expression, licence_paths(dist))


def distribution_index(installed: Iterable[Distribution]) -> DistributionIndex:
    """Return the distributions by the key of each file they installed, and by normalised name (the first one found wins)."""
    by_file: dict[str, DistributionInfo] = {}
    by_name: dict[str, DistributionInfo] = {}
    for dist in installed:
        described = describe_distribution(dist)
        if described.key in by_name:
            continue
        by_name[described.key] = described
        for path in dist.files or ():
            by_file.setdefault(file_key(_located(dist, path)), described)
    return by_file, by_name


def _probe(module: str, attribute: str, shape: Reader) -> str | None:
    try:
        reported = getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError):
        return None
    return shape(reported)


def _nth_word(index: int, reported: object) -> str | None:
    words = str(reported).split()
    return words[index] if len(words) > index else None


def _dotted(reported: object) -> str | None:
    if not isinstance(reported, tuple):
        return None
    return ".".join(map(str, reported))


def _plain(reported: object) -> str | None:
    return str(reported).removeprefix("expat_") if reported else None


# The library versions the interpreter reports: library name, module, attribute, how to read it.
VERSION_PROBES: tuple[tuple[str, str, str, Reader], ...] = (
    ("OpenSSL", "ssl", "OPENSSL_VERSION", partial(_nth_word, 1)),
    ("SQLite", "sqlite3", "sqlite_version", _plain),
    ("zlib", "zlib", "ZLIB_RUNTIME_VERSION", _plain),
    ("Expat", "pyexpat", "EXPAT_VERSION", _plain),
    ("mpdecimal", "decimal", "__libmpdec_version__", _plain),
    ("ncurses", "curses", "ncurses_version", _dotted),
)


def library_versions() -> Mapping[str, str]:
    """Return the versions of the bundled libraries this interpreter reports."""
    found = {}
    for library, module, attribute, shape in VERSION_PROBES:
        version = _probe(module, attribute, shape)
        if version:
            found[library] = version
    return MappingProxyType(found)


def interpreter() -> Interpreter:
    """Return the facts of this interpreter (the one the executable bundles)."""
    prefix = Path(sys.base_prefix)
    stdlib = Path(sysconfig.get_path("stdlib"))
    places = (stdlib / PYTHON_LICENCE_FILE, prefix / PYTHON_LICENCE_FILE)
    licence = next((place for place in places if place.is_file()), None)
    builtins = frozenset(sys.builtin_module_names)
    return Interpreter(prefix, platform.python_version(), licence, builtins, library_versions())


def ffmpeg_build(folder: Path) -> FfmpegBuild:
    """Return the ffmpeg build in `folder` with its description (`ffmpeg-build.json`).

    Raises:
        OSError: The description cannot be read.
        ValueError: The description is no JSON.
        TypeError: The description is no JSON object.
    """
    description = json.loads((folder / BUILD_INFO).read_text(encoding="utf-8"))
    if not isinstance(description, dict):
        msg = f"{folder / BUILD_INFO} holds no JSON object"
        raise TypeError(msg)
    return FfmpegBuild(folder, MappingProxyType(description))


def _folders(listed: object) -> Folders:
    if not isinstance(listed, list):
        return ()
    return tuple(Path(str(folder)) for folder in listed)


def build_facts(config: Mapping[str, object]) -> BuildFacts:
    """Return the facts of this build from the notices configuration `scripts/build_desktop.py` wrote."""
    by_file, by_name = distribution_index(distributions())
    own_folders = _folders(config["own"])
    system = build_system_packages() if sys.platform == "linux" else None
    return BuildFacts(
        platform=sys.platform,
        title=str(config["title"]),
        own_folders=own_folders,
        distributions=MappingProxyType(by_file),
        pyinstaller=by_name.get(normalised(PYINSTALLER)),
        interpreter=interpreter(),
        ffmpeg=ffmpeg_build(Path(str(config["ffmpeg"]))),
        system=system,
    )


def write_config(config_path: Path, *, output: Path, title: str, ffmpeg: Path, own: Folders) -> None:
    """Write the notices configuration of one variant's build, which the PyInstaller runner hands to `notices_entry`.

    `output` is the notices file to write, `title` its first line, `ffmpeg` the ffmpeg build
    the executable bundles, `own` the folders of the app's own build files.
    """
    config = {
        "scripts": str(SCRIPTS_DIR),
        "output": str(output),
        "package": PACKAGE_NAME,
        "title": title,
        "ffmpeg": str(ffmpeg),
        "own": [str(folder) for folder in own],
    }
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def notices_entry(config_path: Path, collected: Iterable[CollectedEntry]) -> tuple[str, str]:
    """Write the notices of the analysed variant; return their name in the bundle and the file written.

    Raises:
        NoticesError: The notices cannot be complete (each problem is named).
    """
    config = json.loads(config_path.read_text(encoding="utf-8"))
    text = generate(collected, build_facts(config))
    output = Path(str(config["output"]))
    output.write_text(text, encoding="utf-8", newline="\n")
    return f"{config['package']}/{NOTICES_FILE}", str(output)
