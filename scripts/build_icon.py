"""Generate the app icon files from their source image (docs/ai/plans/m8.md, M8.3).

Usage, from the repository root (the development environment has PySide6):

    uv run scripts/build_icon.py            # write the generated files
    uv run scripts/build_icon.py --check    # exit 1 naming every committed file that is stale

Renders `apps/desktop/packaging/icon/transcribe-ui.svg` (a speech bubble with two text lines,
Q36) with Qt at each size and writes, deterministically (PNG without timestamps or text chunks):
the window icon PNGs `gui/icons/transcribe-ui-<size>.png` of the app, the Windows
`transcribe-ui.ico` and the macOS `transcribe-ui.icns` of the packaging folder. A designed icon
replaces the SVG and this script regenerates everything else. The container writers are in
`scripts/build_icon_formats.py`.
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from build_icon_formats import build_icns, build_ico, parse_icns, parse_ico
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageWriter, QPainter
from PySide6.QtSvg import QSvgRenderer

# Root of the repository.
REPO_ROOT = Path(__file__).resolve().parent.parent
# The desktop app's workspace member.
DESKTOP_DIR = REPO_ROOT / "apps" / "desktop"
# The source image of the icon.
SOURCE_SVG = DESKTOP_DIR / "packaging" / "icon" / "transcribe-ui.svg"
# Folder of the generated ICO and ICNS.
PACKAGING_ICON_DIR = DESKTOP_DIR / "packaging" / "icon"
# Folder of the window icon PNGs (package data of the GUI).
GUI_ICON_DIR = DESKTOP_DIR / "src" / "transcribe_ui" / "gui" / "icons"
# The PNG file name of a size in the GUI's folder.
PNG_NAME = "transcribe-ui-{size}.png"
# File name of the generated ICO.
ICO_NAME = "transcribe-ui.ico"
# File name of the generated ICNS.
ICNS_NAME = "transcribe-ui.icns"
# The PNG sizes of the GUI folder and of the ICO.
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256)
# The ICNS elements: type and the pixel size of its PNG (the retina types hold twice the nominal size).
ICNS_ELEMENTS = (
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)
# The largest difference of a colour channel (of 255) that still counts as the same pixel: the renderer varies slightly.
PIXEL_TOLERANCE = 2
# The entries of an ICO or ICNS: what identifies the entry and its PNG bytes.
Images = Sequence[tuple[object, bytes]]
# The one-line description of the command.
DESCRIPTION = "Generate the app icon files from their source image."
# PNG compression setting of the Qt writer (0 = smallest, deterministic).
PNG_QUALITY = 0
# Name of the image format of the Qt writer.
PNG_FORMAT = b"png"


def render_png(svg: Path, size: int) -> bytes:
    """Render the SVG square at `size` pixels on a transparent background and return it as PNG bytes."""
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        message = f"cannot read the SVG {svg}"
        raise ValueError(message)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return encode_png(image)


def encode_png(image: QImage) -> bytes:
    """Encode the image as PNG; Qt writes no timestamp and no text chunks, so the bytes are stable."""
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, QByteArray(PNG_FORMAT))
    writer.setQuality(PNG_QUALITY)
    if not writer.write(image):
        message = f"cannot encode the PNG: {writer.errorString()}"
        raise ValueError(message)
    buffer.close()
    return bytes(encoded.data())


def _render_all(svg: Path) -> dict[int, bytes]:
    """Render every size that any of the files needs, once."""
    icns_sizes = {size for _, size in ICNS_ELEMENTS}
    sizes = sorted({*PNG_SIZES, *icns_sizes})
    return {size: render_png(svg, size) for size in sizes}


def build_files(svg: Path, gui_dir: Path, packaging_dir: Path) -> Mapping[Path, bytes]:
    """Return every generated file with its bytes, rendered from the SVG."""
    renders = _render_all(svg)
    names = {size: gui_dir / PNG_NAME.format(size=size) for size in PNG_SIZES}
    files = {names[size]: renders[size] for size in PNG_SIZES}
    ico_images = [renders[size] for size in PNG_SIZES]
    files[packaging_dir / ICO_NAME] = build_ico(ico_images)
    icns_elements = [(kind, renders[size]) for kind, size in ICNS_ELEMENTS]
    files[packaging_dir / ICNS_NAME] = build_icns(icns_elements)
    return files


def _channels(png: bytes) -> tuple[QSize, bytes] | None:
    """Return the size and the channel bytes (non-premultiplied) of the PNG, or None if it cannot be decoded."""
    image = QImage.fromData(png)
    if image.isNull():
        return None
    converted = image.convertToFormat(QImage.Format.Format_ARGB32)
    return converted.size(), bytes(converted.bits())


def _close(pair: tuple[int, int]) -> bool:
    return abs(pair[0] - pair[1]) <= PIXEL_TOLERANCE


def _same_pixels(committed: bytes, generated: bytes) -> bool:
    """Whether two PNGs have the same size and pixels, each channel within `PIXEL_TOLERANCE`."""
    if committed == generated:
        return True
    old, new = _channels(committed), _channels(generated)
    if old is None or new is None:
        return False
    (old_size, old_bytes), (new_size, new_bytes) = old, new
    if old_size != new_size:
        return False
    return all(map(_close, zip(old_bytes, new_bytes, strict=True)))


def _same_pair(pair: tuple[bytes, bytes]) -> bool:
    return _same_pixels(*pair)


def _entries(images: Images) -> list[object]:
    return [entry for entry, _ in images]


def _pngs(images: Images) -> list[bytes]:
    return [png for _, png in images]


def _same_images(committed: Images, generated: Images) -> bool:
    """Whether two container contents have the same entries (type, size) and the same pixels."""
    if _entries(committed) != _entries(generated):
        return False
    pairs = zip(_pngs(committed), _pngs(generated), strict=True)
    return all(map(_same_pair, pairs))


def _same_container(path: Path, committed: bytes, blob: bytes) -> bool:
    parse = parse_ico if path.suffix == ".ico" else parse_icns
    try:
        return _same_images(parse(committed), parse(blob))
    except ValueError:
        return False


def _is_stale(path: Path, blob: bytes) -> bool:
    if not path.is_file():
        return True
    committed = path.read_bytes()
    if path.suffix == ".png":
        return not _same_pixels(committed, blob)
    return not _same_container(path, committed, blob)


def stale_files(files: Mapping[Path, bytes]) -> list[Path]:
    """Return the files that are missing or differ from the generated ones.

    Images are compared by their decoded pixels with a small tolerance per channel (not by bytes,
    which change with Qt's renderer and PNG encoder); the ICO and ICNS also by their entries.
    """
    return [path for path, blob in files.items() if _is_stale(path, blob)]


def main(
    argv: Sequence[str] | None = None,
    *,
    svg: Path = SOURCE_SVG,
    gui_dir: Path = GUI_ICON_DIR,
    packaging_dir: Path = PACKAGING_ICON_DIR,
) -> int:
    """Write the generated icon files, or with `--check` report the stale ones; return the exit code."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 naming each stale file")
    args = parser.parse_args(argv)
    files = build_files(svg, gui_dir, packaging_dir)
    if args.check:
        stale = stale_files(files)
        for path in stale:
            sys.stderr.write(f"stale: {path} (run `uv run scripts/build_icon.py`)\n")
        return 1 if stale else 0
    for path, blob in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
