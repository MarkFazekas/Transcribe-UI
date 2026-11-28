"""Container writers of the app icon: ICO and ICNS holding PNG images (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_icon.py`, which renders the PNGs. Standard library only.
"""

import struct

# The eight bytes every PNG starts with.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Offset of the IHDR width in a PNG (signature, chunk length, chunk type).
PNG_WIDTH_OFFSET = 16
# Size of the PNG prefix that holds the signature and the IHDR width and height.
PNG_HEADER_SIZE = 24
# ICO header: reserved, type (1 = icon), image count.
ICO_HEADER = struct.Struct("<HHH")
# ICO directory entry: width, height, colours, reserved, planes, bits per pixel, size, offset.
ICO_ENTRY = struct.Struct("<BBBBHHII")
# The ICO type of an icon (not a cursor).
ICO_TYPE_ICON = 1
# Bits per pixel recorded for the PNG images of an ICO.
ICO_BITS_PER_PIXEL = 32
# The size the ICO's byte fields cannot hold, written as 0 (the format's convention).
ICO_BYTE_LIMIT = 256
# The magic of an ICNS file.
ICNS_MAGIC = b"icns"
# ICNS element and file header: four-character type and the big-endian length including itself.
ICNS_HEADER = struct.Struct(">4sI")


def png_size(png: bytes) -> tuple[int, int]:
    """Return the width and height of a PNG from its IHDR chunk."""
    if len(png) < PNG_HEADER_SIZE or not png.startswith(PNG_SIGNATURE):
        message = "not a PNG image"
        raise ValueError(message)
    width, height = struct.unpack(">II", png[PNG_WIDTH_OFFSET:PNG_HEADER_SIZE])
    return width, height


def build_ico(images: list[bytes]) -> bytes:
    """Return an ICO file holding the PNG images, in the order given."""
    offset = ICO_HEADER.size + ICO_ENTRY.size * len(images)
    entries = []
    for image in images:
        width, height = png_size(image)
        entries.append(
            ICO_ENTRY.pack(
                width % ICO_BYTE_LIMIT,
                height % ICO_BYTE_LIMIT,
                0,
                0,
                1,
                ICO_BITS_PER_PIXEL,
                len(image),
                offset,
            ),
        )
        offset += len(image)
    header = ICO_HEADER.pack(0, ICO_TYPE_ICON, len(images))
    return b"".join([header, *entries, *images])


def _icns_element(kind: str, image: bytes) -> bytes:
    length = ICNS_HEADER.size + len(image)
    header = ICNS_HEADER.pack(kind.encode("ascii"), length)
    return header + image


def build_icns(elements: list[tuple[str, bytes]]) -> bytes:
    """Return an ICNS file of PNG elements, each given as its four-character type and its PNG bytes."""
    parts = [_icns_element(kind, image) for kind, image in elements]
    body = b"".join(parts)
    length = ICNS_HEADER.size + len(body)
    return ICNS_HEADER.pack(ICNS_MAGIC, length) + body
