"""The icon generator: its container writers, the rendering of the committed SVG and `--check` (M8.3)."""

import shutil
import struct
from pathlib import Path

import pytest
from build_icon import (
    GUI_ICON_DIR,
    ICNS_ELEMENTS,
    ICNS_NAME,
    ICO_NAME,
    PACKAGING_ICON_DIR,
    PNG_NAME,
    PNG_SIZES,
    SOURCE_SVG,
    main,
    render_png,
)
from build_icon_formats import PNG_SIGNATURE, build_icns, build_ico, png_size
from PySide6.QtGui import QColor, QImage

# The colour of the icon in the SVG.
ICON_COLOUR = QColor("#1565C0")
# Sizes (pixels) of the synthetic PNGs.
SMALL, MEDIUM, LARGE = 16, 48, 256
# Size of the ICO header and of one directory entry.
ICO_HEADER_SIZE, ICO_ENTRY_SIZE = 6, 16
# Size of the ICNS file header and of an element header.
ICNS_HEADER_SIZE = 8
# Length of the data of an IHDR chunk.
IHDR_LENGTH = 13
# The largest ICNS element, in pixels.
LARGEST = 1024
# A fully opaque alpha value.
OPAQUE = 255
# The sixteenth of the icon's side at which the bubble is solid below its lines, and a text line.
BUBBLE_ROW, LINE_ROW, GRID = 12, 5, 16
# Sizes at which the rendering is checked.
RENDER_SIZES = (SMALL, LARGE)


def _png(size: int) -> bytes:
    """Return the start of a PNG of `size` x `size` pixels: signature, IHDR chunk header, size, padding."""
    return b"".join([PNG_SIGNATURE, struct.pack(">I4sII", IHDR_LENGTH, b"IHDR", size, size), bytes(size)])


def _image(size: int) -> QImage:
    image = QImage.fromData(render_png(SOURCE_SVG, size))
    assert not image.isNull()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _entry(ico: bytes, index: int) -> tuple[int, ...]:
    """Return the fields of an ICO directory entry: width, height, colours, reserved, planes, bits, size, offset."""
    return struct.unpack_from("<BBBBHHII", ico, ICO_HEADER_SIZE + ICO_ENTRY_SIZE * index)


def _slice(payload: bytes, start: int, length: int) -> bytes:
    end = start + length
    return payload[start:end]


def _folders(tmp_path: Path) -> dict[str, Path]:
    return {"svg": SOURCE_SVG, "gui_dir": tmp_path / "gui", "packaging_dir": tmp_path / "packaging"}


def test_png_size_reads_the_header() -> None:
    """The size comes from the IHDR chunk; anything else is refused."""
    assert png_size(_png(MEDIUM)) == (MEDIUM, MEDIUM)
    with pytest.raises(ValueError, match="not a PNG"):
        png_size(bytes(LARGE))


def test_ico_header_and_entries_parse_back() -> None:
    """The header counts the images; each entry has its size (0 for 256), length and offset."""
    images = [_png(SMALL), _png(MEDIUM), _png(LARGE)]
    ico = build_ico(images)
    assert struct.unpack_from("<HHH", ico, 0) == (0, 1, len(images))
    for index, image in enumerate(images):
        entry = _entry(ico, index)
        side = png_size(image)[0] % LARGE
        assert entry[:2] == (side, side)
        assert entry[4:7] == (1, 32, len(image))
        assert _slice(ico, entry[-1], len(image)) == image


def test_ico_offsets_follow_each_other() -> None:
    """The first image follows the directory; the next follows the first."""
    images = [_png(SMALL), _png(LARGE)]
    ico = build_ico(images)
    first = ICO_HEADER_SIZE + ICO_ENTRY_SIZE * len(images)
    offsets = [_entry(ico, index)[-1] for index in range(len(images))]
    assert offsets == [first, first + len(images[0])]
    assert ico[ICO_HEADER_SIZE + ICO_ENTRY_SIZE] == 0


def test_icns_elements_parse_back() -> None:
    """The file and every element carry their own length; elements keep type and bytes."""
    elements = [("icp4", _png(SMALL)), ("ic08", _png(LARGE))]
    icns = build_icns(elements)
    assert icns[:4] == b"icns"
    assert struct.unpack_from(">I", icns, 4)[0] == len(icns)
    position = ICNS_HEADER_SIZE
    for kind, image in elements:
        found, length = struct.unpack_from(">4sI", icns, position)
        assert (found, length) == (kind.encode(), ICNS_HEADER_SIZE + len(image))
        assert _slice(icns, position + ICNS_HEADER_SIZE, length - ICNS_HEADER_SIZE) == image
        position += length
    assert position == len(icns)


@pytest.mark.parametrize("size", RENDER_SIZES)
def test_render_is_transparent_at_the_corners(size: int) -> None:
    """The rendering has the asked size and no background."""
    image = _image(size)
    assert (image.width(), image.height()) == (size, size)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(size - 1, size - 1).alpha() == 0


@pytest.mark.parametrize("size", RENDER_SIZES)
def test_render_is_solid_and_cut_at_the_lines(size: int) -> None:
    """Below the lines the bubble is opaque in the icon colour; a text line shows the background."""
    image = _image(size)
    solid = image.pixelColor(size // 2, size * BUBBLE_ROW // GRID)
    assert solid.alpha() == OPAQUE
    assert solid.name() == ICON_COLOUR.name()
    assert image.pixelColor(size // 2, size * LINE_ROW // GRID).alpha() == 0


def test_check_passes_on_the_committed_files() -> None:
    """The committed generated files are what the generator renders from the committed SVG."""
    assert main(["--check"]) == 0


def test_check_names_a_changed_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A copy with one changed file fails, naming that file only; a plain run repairs it."""
    folders = _folders(tmp_path)
    shutil.copytree(GUI_ICON_DIR, folders["gui_dir"])
    shutil.copytree(PACKAGING_ICON_DIR, folders["packaging_dir"])
    changed = folders["gui_dir"] / PNG_NAME.format(size=PNG_SIZES[0])
    changed.write_bytes(changed.read_bytes()[:-1])
    assert main(["--check"], **folders) == 1
    err = capsys.readouterr().err
    assert changed.name in err
    assert err.count("stale:") == 1
    assert main([], **folders) == 0
    assert main(["--check"], **folders) == 0


def test_check_reports_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Empty output folders are all stale: every PNG, the ICO and the ICNS."""
    assert main(["--check"], **_folders(tmp_path)) == 1
    err = capsys.readouterr().err
    assert ICO_NAME in err
    assert ICNS_NAME in err
    assert err.count("stale:") == len(PNG_SIZES) + 2


def test_icns_has_the_eleven_types() -> None:
    """The ICNS holds the PNG types from 16 up to 1024 pixels, each type once."""
    kinds = [kind for kind, _ in ICNS_ELEMENTS]
    assert len(kinds) == len(set(kinds)) == len(ICNS_ELEMENTS)
    assert max(size for _, size in ICNS_ELEMENTS) == LARGEST
