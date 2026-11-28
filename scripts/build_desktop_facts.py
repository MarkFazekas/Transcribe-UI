"""The facts of the build machine the third-party notices are made from (docs/ai/plans/m8.md, M8.3).

Runs inside the PyInstaller process of one variant (`apps/desktop/packaging/pyinstaller_main.py`),
whose interpreter and environment are the ones the executable bundles: the installed
distributions (their `RECORD` files and licence metadata), the interpreter (its prefix, licence,
built-in modules and the versions of the libraries it reports), the ffmpeg build's description
and, on Linux, the OS package database. `notices_entry` writes the notices and returns the entry
of the collected files that bundles them. Standard library only.
"""

import json
import os
import sys
from collections.abc import Iterable, Mapping
from importlib.metadata import distributions
from pathlib import Path
from types import MappingProxyType

from build_desktop_distributions import distribution_index
from build_desktop_interpreter import interpreter
from build_desktop_licences import PYINSTALLER
from build_desktop_notices import CollectedEntry, generate
from build_desktop_notices_classes import BuildFacts, FfmpegBuild, normalised
from build_desktop_packages import build_system_packages
from build_ffmpeg_steps import BUILD_INFO

# The environment variables.
type Environment = Mapping[str, str]
# A sequence of folders.
type Folders = tuple[Path, ...]
# Name of the notices file inside the app's package (`transcribe_ui.constants.THIRD_PARTY_NOTICES_FILE`).
NOTICES_FILE = "THIRD_PARTY_NOTICES.txt"
# The app's top-level package; the notices are bundled as its data.
PACKAGE_NAME = "transcribe_ui"
# The library folders of a Linux system, from which the build container's packages are bundled.
LINUX_LIBRARY_FOLDERS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64")
# This folder, which the PyInstaller runner puts on its module search path after the analysis.
SCRIPTS_DIR = Path(__file__).resolve().parent


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


def binary_folders(platform: str = sys.platform, environment: Environment = os.environ) -> Folders:
    r"""Return where a collected binary may come from besides the interpreter and the app's own folders.

    The build environment always; on Linux the OS's library folders (the build container's
    packages); on Windows the OS's own folder (`%SystemRoot%\System32`, Microsoft's runtime). A
    tool folder that is on `PATH` (Git for Windows' OpenSSL, Homebrew) is never one.
    """
    folders = [Path(sys.prefix)]
    if platform == "linux":
        folders.extend(Path(folder) for folder in LINUX_LIBRARY_FOLDERS)
    system_root = environment.get("SystemRoot")
    if platform == "win32" and system_root:
        folders.append(Path(system_root) / "System32")
    return tuple(folders)


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
        binary_folders=binary_folders(),
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
