"""The macOS steps of a desktop build, and the GUI as `Transcribe-UI.app` in a disk image (A23; plans/m8.md M8.4).

Used by `scripts/build_desktop.py` after a macOS build: the floor check of both variants
(`scripts/build_desktop_macos.py`), and for the GUI the app's `Info.plist` and its disk image.
PyInstaller builds the app onedir (`--onedir --windowed`) and signs it ad hoc. Its command line
sets no `Info.plist` entry but the bundle identifier, so `prepare_app` writes the entries of
`info_plist` to a plist file, `apps/desktop/packaging/pyinstaller_main.py` hands them to
PyInstaller's `BUNDLE` (which writes them before it signs the bundle), and `macos_report` checks
the written `Info.plist` afterwards. PyInstaller only warns when it cannot sign the bundle, so
the build runs it with `PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR` (a failed signature fails the
build) and `PYINSTALLER_VERIFY_BUNDLE_SIGNATURE` (it verifies the signed bundle). The disk image holds the app and an `Applications` link, so
the user drags one onto the other. It is made with `hdiutil` in the `ULFO` format (lzfse):
smaller than `UDZO` at its default zlib level and faster to open, and readable since macOS
10.11, far below the release's floor of 14. `hdiutil create` sometimes fails with "Resource
busy" on hosted runners, so it is tried a few times; between two tries a device it left
attached (found through `hdiutil info`) is detached, as far as that works.
"""

import os
import plistlib
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from types import MappingProxyType
from xml.parsers.expat import ExpatError

from build_desktop_bundle import read_info_plist
from build_desktop_macos import MACOS_FLOOR, FloorReport, check_app, check_bundle

# Entries of the app's `Info.plist` that the build sets.
type PlistEntries = Mapping[str, str | bool]
# One attached image as `hdiutil info -plist` lists it.
type AttachedImage = Mapping[str, object]
# What identifies an attached image across two listings: its path and its sorted devices.
type AttachedKey = tuple[str, tuple[str, ...]]

# Name of the app: PyInstaller's `--name` of the macOS GUI, the bundle's and the disk image's volume name.
MACOS_APP_NAME = "Transcribe-UI"
# Folder name of the app bundle.
APP_BUNDLE = f"{MACOS_APP_NAME}.app"
# The app's bundle identifier. The keychain does not go by it: an ad hoc signed app's access to an
# entry follows its code hash, so each new build may ask for keychain access again.
BUNDLE_IDENTIFIER = "io.github.markfazekas.transcribe-ui"
# Icon of the app (`.icns`); None keeps PyInstaller's default icon. The icon part (M8.3) sets the project's icon here.
APP_ICON: Path | None = None
# Environment variable naming the plist file whose entries PyInstaller writes into the app's `Info.plist`.
INFO_PLIST_VARIABLE = "TRANSCRIBE_UI_INFO_PLIST"
# PyInstaller's variables that make a failed signature of the app fail the build and verify the signed app.
CODESIGN_VARIABLES: Mapping[str, str] = MappingProxyType(
    {"PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR": "1", "PYINSTALLER_VERIFY_BUNDLE_SIGNATURE": "1"},
)
# The local part of a stamped version, `<base>+<YYYYMMDD>.<short sha>`: the commit date and hash.
STAMP_LOCAL = re.compile(r"(\d{8})\.[0-9a-f]{7,40}")
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
# Seconds one `hdiutil create` may take. On the Intel runner the floor check, PyInstaller's signature
# check and `hdiutil create` together took up to 224 s, so one try may well pass 150 s.
HDIUTIL_TIMEOUT_S = 600
# Seconds `hdiutil info` or the detach of a leftover device may take.
HDIUTIL_CLEANUP_TIMEOUT_S = 60
# Where a volume named after the app is mounted (`/Volumes/Transcribe-UI`, or with a number appended).
APP_VOLUME = f"/Volumes/{MACOS_APP_NAME}"


def is_macos_app(variant: str, tag: str) -> bool:
    """Return whether the variant is built as a macOS `.app` in a disk image (the GUI on macOS, A23)."""
    return variant == "gui" and tag.startswith("macos-")


def bundle_name(stem: str, *, app: bool) -> str:
    """Return PyInstaller's `--name`: the published file's stem, or the macOS app's name."""
    return MACOS_APP_NAME if app else stem


def release_segment(version: str) -> str:
    """Return the leading release segment of a PEP 440 version (`2.0.0` of `2.0.0rc1`).

    Raises:
        ValueError: The version does not start with a release segment.
    """
    found = RELEASE_SEGMENT.match(version)
    if found is None:
        msg = f"{version!r} has no release segment"
        raise ValueError(msg)
    return found.group()


def bundle_versions(base: str, version: str) -> tuple[str, str]:
    """Return `CFBundleShortVersionString` and `CFBundleVersion` of the stamped version `<base>+<YYYYMMDD>.<sha>`.

    The first is the release segment of its public part (numbers only, as Apple asks), the
    second its commit date as `YYYY.M.D` (Apple: up to three integers).

    Raises:
        ValueError: The version is not `base` stamped (`build_desktop.py stamp` prints it), or
            its date is no date.
    """
    public, _plus, local = version.partition("+")
    stamped = STAMP_LOCAL.fullmatch(local)
    if public != base or stamped is None:
        msg = f"{version!r} is not a stamped version of {base} ({base}+<YYYYMMDD>.<short sha>)"
        raise ValueError(msg)
    day = date.fromisoformat(stamped.group(1))
    dated = f"{day.year}.{day.month}.{day.day}"
    return release_segment(public), dated


def info_plist(base: str, version: str) -> dict[str, str | bool]:
    """Return the `Info.plist` entries the build sets (PyInstaller adds the bundle's name, executable and icon).

    Raises:
        ValueError: The version is not a stamped one of `base`, or `base` has no release segment.
    """
    short, dated = bundle_versions(base, version)
    return {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleShortVersionString": short,
        "CFBundleVersion": dated,
        "LSMinimumSystemVersion": ".".join(map(str, MACOS_FLOOR)),
        "NSHighResolutionCapable": True,
    }


def prepare_app(base: str, version: str, plist_file: Path) -> dict[str, str]:
    """Write the app's `Info.plist` entries to `plist_file`; return what PyInstaller's environment needs.

    That is the plist file's variable and the strict signing and verification of the app.

    Raises:
        RuntimeError: The version is not a stamped one of `base`, or `base` has no release segment.
    """
    try:
        entries = info_plist(base, version)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    with plist_file.open("wb") as stream:
        plistlib.dump(entries, stream)
    return {INFO_PLIST_VARIABLE: str(plist_file), **CODESIGN_VARIABLES}


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


def _strings(entries: object, key: str) -> list[str]:
    """Return the text values of `key` in a list of plist dictionaries (anything else is skipped)."""
    if not isinstance(entries, list):
        return []
    found = [entry.get(key) for entry in entries if isinstance(entry, dict)]
    return [text for text in found if isinstance(text, str)]


def _is_app_volume(mount_point: str) -> bool:
    """Return whether a volume named after the app is mounted there (`/Volumes/Transcribe-UI`, `... 1`)."""
    numbered = mount_point.removeprefix(f"{APP_VOLUME} ")
    return mount_point == APP_VOLUME or (numbered != mount_point and numbered.isdigit())


def _attached_key(attached: AttachedImage) -> AttachedKey:
    """Return what identifies an attached image across two listings: its path and its devices."""
    path = attached.get("image-path")
    entities = attached.get("system-entities")
    disks = tuple(sorted(_strings(entities, "dev-entry")))
    return (path if isinstance(path, str) else "", disks)


def _leftover(attached: AttachedImage, image: Path) -> str | None:
    """Return the whole-disk device of an attached image if it is `image` or holds the app's volume, else None."""
    entities = attached.get("system-entities")
    path = attached.get("image-path")
    source = Path(path).resolve() if isinstance(path, str) else None
    same_image = source == image.resolve()
    mount_points = _strings(entities, "mount-point")
    named = any(_is_app_volume(mount) for mount in mount_points)
    disks = _strings(entities, "dev-entry")
    if not disks or not (same_image or named):
        return None
    return min(disks, key=len)


def leftover_devices(before: Sequence[AttachedImage], after: Sequence[AttachedImage], image: Path) -> list[str]:
    """Return the device of each image attached since `before` that a failed `hdiutil create` of `image` left.

    Both are the images of `hdiutil info -plist`. Only an image that is new since `before` (by its
    path and devices) counts, so a volume attached before the try (a downloaded release image
    with the app running from it) is never touched. Of those, `image` itself and an image with a
    volume named after the app (the one `hdiutil create -srcfolder` fills) count, each by its
    shortest device entry, the whole disk.
    """
    known = {_attached_key(attached) for attached in before}
    new = [attached for attached in after if _attached_key(attached) not in known]
    found = [_leftover(attached, image) for attached in new]
    return [device for device in found if device is not None]


def _run_quietly(command: list[str]) -> bytes | None:
    """Run a clean-up command; return its output, or None when it could not run or failed."""
    try:
        completed = subprocess.run(command, capture_output=True, timeout=HDIUTIL_CLEANUP_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if completed.returncode else completed.stdout


def attached_images() -> list[AttachedImage] | None:
    """Return the attached images (`hdiutil info -plist`), or None when they cannot be listed (nothing is raised)."""
    listed = _run_quietly([HDIUTIL, "info", "-plist"])
    if listed is None:
        return None
    try:
        parsed = plistlib.loads(listed)
    except (plistlib.InvalidFileException, ValueError, ExpatError):
        return None
    images = parsed.get("images") if isinstance(parsed, dict) else None
    if not isinstance(images, list):
        return None
    return [attached for attached in images if isinstance(attached, dict)]


def detach_leftovers(image: Path, before: Sequence[AttachedImage] | None) -> None:
    """Detach, forced, what a failed `hdiutil create` of `image` left attached since `before`; nothing is raised.

    Without a listing from before the try (`before` None) nothing is detached: an image attached
    earlier could not be told apart.
    """
    after = attached_images()
    if before is None or after is None:
        return
    for device in leftover_devices(before, after, image):
        sys.stdout.write(f"Detaching {device}, left attached by hdiutil\n")
        _run_quietly([HDIUTIL, "detach", "-force", device])


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
        before = attached_images()
        started = time.monotonic()
        error = _create(command)
        seconds = time.monotonic() - started
        outcome = error or "done"
        sys.stdout.write(f"hdiutil create, try {attempt}: {seconds:.1f} s, {outcome}\n")
        # Flushed, so the job log's time stamps show when each try ended.
        sys.stdout.flush()
        if error is None:
            return image
        errors.append(f"try {attempt}: {error}")
        if attempt < IMAGE_ATTEMPTS:
            detach_leftovers(image, before)
            time.sleep(IMAGE_RETRY_PAUSE_S)
    report = os.linesep.join(errors)
    msg = f"hdiutil could not create {image}:{os.linesep}{report}"
    raise RuntimeError(msg)
