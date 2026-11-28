"""The macOS GUI's disk image in the desktop smoke test (A23; docs/ai/plans/m8.md M8.4).

Used by `scripts/smoke_desktop_build.py`. The image is attached read-only at a fresh folder,
never shown in Finder (`-nobrowse`); it must hold `Transcribe-UI.app` and the `Applications` link
and nothing else, and the GUI checks run the app's main executable from it. It is detached when
the run ends, however it ends (forced when a process still holds it); a detach that fails is
reported, not raised, so it cannot hide the checks' outcome.
"""

import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from build_desktop_app import APP_BUNDLE, APPLICATIONS_LINK, APPLICATIONS_TARGET, HDIUTIL
from build_desktop_bundle import app_executable
from smoke_desktop_process import run_process

# Suffix of a disk image.
IMAGE_SUFFIX = ".dmg"
# Seconds one `hdiutil attach` or `detach` may take.
HDIUTIL_TIMEOUT_S = 300
# What the image holds besides hidden files: the app and the link to drag it onto.
IMAGE_ENTRIES = frozenset((APP_BUNDLE, APPLICATIONS_LINK))


class ImageError(RuntimeError):
    """The disk image could not be attached, or it does not hold the app as published."""


def is_disk_image(path: Path) -> bool:
    """Return whether the file is a disk image (the macOS GUI) rather than an executable."""
    return path.suffix == IMAGE_SUFFIX


def attach_command(image: Path, mount_point: Path) -> list[str]:
    """Return the `hdiutil` command that attaches the image read-only at `mount_point`, hidden from Finder."""
    return [
        HDIUTIL,
        "attach",
        str(image),
        "-readonly",
        "-nobrowse",
        "-noautoopen",
        "-mountpoint",
        str(mount_point),
    ]


def detach_command(mount_point: Path, *, force: bool) -> list[str]:
    """Return the `hdiutil` command that detaches the image at `mount_point` (forced: even while it is in use)."""
    forced = ["-force"] if force else []
    return [HDIUTIL, "detach", str(mount_point), *forced]


def image_problems(root: Path) -> list[str]:
    """Return what the mounted image lacks or should not hold: exactly the app and the `Applications` link."""
    entries = [entry.name for entry in root.iterdir()]
    names = {name for name in entries if not name.startswith(".")}
    problems = [f"the image lacks {name}" for name in sorted(IMAGE_ENTRIES - names)]
    extra = sorted(names - IMAGE_ENTRIES)
    problems.extend(f"the image also holds {name}" for name in extra)
    link = root / APPLICATIONS_LINK
    if APPLICATIONS_LINK in names and not link.is_symlink():
        problems.append(f"{APPLICATIONS_LINK} is no link")
    elif link.is_symlink() and link.readlink() != Path(APPLICATIONS_TARGET):
        problems.append(f"{APPLICATIONS_LINK} points at {link.readlink()}, not {APPLICATIONS_TARGET}")
    return problems


def detach(mount_point: Path) -> None:
    """Detach the image at `mount_point` if it is mounted, forced when a plain detach fails; report a failure."""
    if not mount_point.is_mount():
        return
    plain = run_process(detach_command(mount_point, force=False), os.environ, HDIUTIL_TIMEOUT_S)
    if plain.returncode == 0:
        return
    forced = run_process(detach_command(mount_point, force=True), os.environ, HDIUTIL_TIMEOUT_S)
    if forced.returncode != 0:
        outputs = f"{plain.text()}\n{forced.text()}"
        sys.stderr.write(f"warning: {mount_point} could not be detached\n{outputs}\n")


def attach(image: Path, stack: ExitStack) -> Path:
    """Attach the image read-only at a fresh folder; return it. Closing `stack` detaches it and removes the folder.

    Raises:
        ImageError: `hdiutil` could not attach the image.
    """
    folder = stack.enter_context(tempfile.TemporaryDirectory(prefix="transcribe-ui-image-", ignore_cleanup_errors=True))
    mount_point = Path(folder) / "volume"
    mount_point.mkdir()
    # Registered first, so even an attach that times out after mounting is detached.
    stack.callback(detach, mount_point)
    attached = run_process(attach_command(image, mount_point), os.environ, HDIUTIL_TIMEOUT_S)
    if attached.returncode != 0:
        msg = f"hdiutil could not attach {image} (exit {attached.returncode})\n{attached.text()}"
        raise ImageError(msg)
    return mount_point


def gui_executable(published: Path, stack: ExitStack) -> Path:
    """Return the GUI executable the checks run: the file itself, or the app's in the attached disk image.

    Closing `stack` detaches the image.

    Raises:
        ImageError: The image could not be attached, or it does not hold the app as published.
    """
    if not is_disk_image(published):
        return published
    root = attach(published, stack)
    problems = image_problems(root)
    if problems:
        raise ImageError("\n".join(problems))
    try:
        return app_executable(root / APP_BUNDLE)
    except (OSError, ValueError) as error:
        msg = f"{published}: {error}"
        raise ImageError(msg) from error
