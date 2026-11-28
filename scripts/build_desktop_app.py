"""The macOS steps of a desktop build, and the GUI as `Transcribe-UI.app` in a disk image (A23; plans/m8.md M8.4).

Used by `scripts/build_desktop.py` after a macOS build: the floor check of both variants
(`scripts/build_desktop_macos.py`), and for the GUI the app's `Info.plist` and its disk image.
PyInstaller builds the app onedir (`--onedir --windowed`) and signs it ad hoc. Its command line
sets no `Info.plist` entry but the bundle identifier, so `prepare_app` writes the entries of
`info_plist` to a plist file, `apps/desktop/packaging/pyinstaller_main.py` hands them to
PyInstaller's `BUNDLE` (which writes them before it signs the bundle), and `macos_report` checks
the written `Info.plist` afterwards. The disk image holds the app and an `Applications` link, so
the user drags one onto the other. It is made with `hdiutil` in the `ULFO` format (lzfse):
smaller than `UDZO` at its default zlib level and faster to open, and readable since macOS
10.11, far below the release's floor of 14. `hdiutil create` sometimes fails with "Resource
busy" on hosted runners, so it is tried a few times.
"""

import os
import plistlib
import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from build_desktop_bundle import read_info_plist
from build_desktop_macos import MACOS_FLOOR, FloorReport, check_app, check_bundle

# Entries of the app's `Info.plist` that the build sets.
type PlistEntries = Mapping[str, str | bool]

# Name of the app: PyInstaller's `--name` of the macOS GUI, the bundle's and the disk image's volume name.
MACOS_APP_NAME = "Transcribe-UI"
# Folder name of the app bundle.
APP_BUNDLE = f"{MACOS_APP_NAME}.app"
# The app's bundle identifier (also its identity for the keychain).
BUNDLE_IDENTIFIER = "io.github.markfazekas.transcribe-ui"
# Icon of the app (`.icns`); None keeps PyInstaller's default icon. The icon part (M8.3) sets the project's icon here.
APP_ICON: Path | None = None
# Environment variable naming the plist file whose entries PyInstaller writes into the app's `Info.plist`.
INFO_PLIST_VARIABLE = "TRANSCRIBE_UI_INFO_PLIST"
# The commit date of a stamped version, `<base>+<YYYYMMDD>.<short sha>`.
STAMPED_DAY = re.compile(r"\+(\d{4})(\d{2})(\d{2})\.[0-9a-f]+")
# Leading release segment of a PEP 440 version.
RELEASE_SEGMENT = re.compile(r"\d+(?:\.\d+)*")
# PyInstaller's table of contents of the app (`BUNDLE`), in its work folder.
APP_TABLE = "BUNDLE-00.toc"
# PyInstaller's table of contents of the package of a one-file executable, in its work folder.
PACKAGE_TABLE = "PKG-00.toc"
# The link in the disk image that the user drags the app onto.
APPLICATIONS_LINK = "Applications"
# Target of the `Applications` link.
APPLICATIONS_TARGET = "/Applications"
# Apple's disk image tool (every macOS has it there).
HDIUTIL = "/usr/bin/hdiutil"
# Format of the disk image: compressed with lzfse, read-only.
IMAGE_FORMAT = "ULFO"
# File system of the disk image.
IMAGE_FILE_SYSTEM = "HFS+"
# Times `hdiutil create` is tried.
IMAGE_ATTEMPTS = 3
# Seconds between two tries of `hdiutil create`.
IMAGE_RETRY_PAUSE_S = 15
# Seconds one `hdiutil create` may take.
HDIUTIL_TIMEOUT_S = 900


def is_macos_app(variant: str, tag: str) -> bool:
    """Return whether the variant is built as a macOS `.app` in a disk image (the GUI on macOS, A23)."""
    return variant == "gui" and tag.startswith("macos-")


def bundle_name(stem: str, *, app: bool) -> str:
    """Return PyInstaller's `--name`: the published file's stem, or the macOS app's name."""
    return MACOS_APP_NAME if app else stem


def bundle_date(version: str) -> str:
    """Return `CFBundleVersion` of a stamped version: its commit date as `YYYY.M.D` (Apple: up to three integers).

    Raises:
        ValueError: The version is not a stamped one (`build_desktop.py stamp` prints it).
    """
    found = STAMPED_DAY.search(version)
    if found is None:
        msg = f"{version!r} is not a stamped version (<base>+<YYYYMMDD>.<short sha>)"
        raise ValueError(msg)
    numbers = [int(part) for part in found.groups()]
    return ".".join(map(str, numbers))


def short_version(base: str) -> str:
    """Return `CFBundleShortVersionString`: the release segment of the base version (numbers only, as Apple asks).

    Raises:
        ValueError: The version does not start with a release segment.
    """
    found = RELEASE_SEGMENT.match(base)
    if found is None:
        msg = f"{base!r} has no release segment"
        raise ValueError(msg)
    return found.group()


def info_plist(base: str, version: str) -> dict[str, str | bool]:
    """Return the `Info.plist` entries the build sets (PyInstaller adds the bundle's name, executable and icon).

    Raises:
        ValueError: The version is not a stamped one, or the base version has no release segment.
    """
    return {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleShortVersionString": short_version(base),
        "CFBundleVersion": bundle_date(version),
        "LSMinimumSystemVersion": ".".join(map(str, MACOS_FLOOR)),
        "NSHighResolutionCapable": True,
    }


def prepare_app(base: str, version: str, plist_file: Path) -> dict[str, str]:
    """Write the app's `Info.plist` entries to `plist_file`; return what PyInstaller's environment needs for them.

    Raises:
        RuntimeError: The version is not a stamped one, or the base version has no release segment.
    """
    try:
        entries = info_plist(base, version)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    with plist_file.open("wb") as stream:
        plistlib.dump(entries, stream)
    return {INFO_PLIST_VARIABLE: str(plist_file)}


def plist_problems(app: Path, expected: PlistEntries) -> list[str]:
    """Return every entry of the app's `Info.plist` that differs from `expected` (empty: none)."""
    written = read_info_plist(app)
    return [
        f"Info.plist: {key} is {written.get(key)!r}, expected {wanted!r}"
        for key, wanted in expected.items()
        if written.get(key) != wanted
    ]


def macos_report(built: Path, tables: Path, plist_file: Path | None) -> FloorReport:
    """Check a macOS build: the floor, and for the app (`plist_file` given) the `Info.plist` entries the build set.

    `tables` is PyInstaller's work folder of the build, which holds its tables of contents.

    Raises:
        RuntimeError: The app's `Info.plist` names no executable, or `otool` could not read a binary.
        subprocess.SubprocessError: Reading a binary did not finish in time.
    """
    if plist_file is None:
        return check_bundle(tables / PACKAGE_TABLE, built)
    try:
        report = check_app(tables / APP_TABLE, built)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    with plist_file.open("rb") as stream:
        expected = plistlib.load(stream)
    problems = (*report.problems, *plist_problems(built, expected))
    return FloorReport(problems, report.wheels)


def create_image_command(staging: Path, image: Path) -> list[str]:
    """Return the `hdiutil` command that packs the folder `staging` into the compressed disk image `image`."""
    return [
        HDIUTIL,
        "create",
        "-volname",
        MACOS_APP_NAME,
        "-srcfolder",
        str(staging),
        "-fs",
        IMAGE_FILE_SYSTEM,
        "-format",
        IMAGE_FORMAT,
        "-ov",
        str(image),
    ]


def stage_image(app: Path, staging: Path) -> None:
    """Move the app into the new folder `staging`, next to the `Applications` link."""
    staging.mkdir(parents=True)
    app.rename(staging / app.name)
    (staging / APPLICATIONS_LINK).symlink_to(APPLICATIONS_TARGET)


def _create(command: list[str]) -> str | None:
    """Run `hdiutil create` once; return its error, or None when it succeeded."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=HDIUTIL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {HDIUTIL_TIMEOUT_S} s"
    if completed.returncode:
        return f"exit {completed.returncode}: {completed.stderr.strip()}"
    return None


def make_disk_image(app: Path, staging: Path, image: Path) -> Path:
    """Pack the app (moved into `staging`) and an `Applications` link into the disk image `image`; return it.

    Raises:
        RuntimeError: `hdiutil` failed on every try.
    """
    stage_image(app, staging)
    image.parent.mkdir(parents=True, exist_ok=True)
    command = create_image_command(staging, image)
    errors = []
    for attempt in range(1, IMAGE_ATTEMPTS + 1):
        error = _create(command)
        if error is None:
            return image
        errors.append(f"try {attempt}: {error}")
        if attempt < IMAGE_ATTEMPTS:
            time.sleep(IMAGE_RETRY_PAUSE_S)
    report = os.linesep.join(errors)
    msg = f"hdiutil could not create {image}:{os.linesep}{report}"
    raise RuntimeError(msg)
