"""The layout of the macOS GUI's `.app` bundle as PyInstaller writes it (A23; docs/ai/plans/m8.md M8.4).

Used by the build's floor check (`scripts/build_desktop_macos.py`), its `Info.plist` check
(`scripts/build_desktop_app.py`) and the smoke test (`scripts/smoke_desktop_bundle.py`,
`scripts/smoke_desktop_image.py`). PyInstaller puts the main executable in `Contents/MacOS`,
binaries under `Contents/Frameworks` and data under `Contents/Resources`, each by its name in the
bundle, with links between the two folders; under `Frameworks` a dot in a folder name is
replaced by `DOT_REPLACEMENT` (code signing would take such a folder for a nested bundle), and the
dotted name stays as a link.
"""

import plistlib
from pathlib import Path, PurePosixPath

# What PyInstaller puts in place of a dot in a folder name under `Contents/Frameworks`.
DOT_REPLACEMENT = "__dot__"
# Folders of `Contents` that hold the bundled files: binaries and data.
CONTENT_FOLDERS = frozenset(("Frameworks", "Resources"))
# Parts of a bundled file's path inside `Contents`: its content folder, then at least its name.
CONTENT_PATH_PARTS = 2
# First four bytes of a thin Mach-O file (32 or 64 bits, either byte order) or a 64-bit universal one.
MACH_O_MAGICS = frozenset((0xFEEDFACE, 0xCEFAEDFE, 0xFEEDFACF, 0xCFFAEDFE, 0xCAFEBABF))
# First four bytes of a universal Mach-O file, which a Java class file shares.
UNIVERSAL_MAGIC = 0xCAFEBABE
# Architectures a universal file has fewer of; the same word of a Java class file is its version (45 or more).
UNIVERSAL_ARCHITECTURES_LIMIT = 30
# Bytes of a Mach-O header read to recognise it: the magic and the next word.
MACH_O_HEADER_BYTES = 8
# Bytes of the magic at the start of a Mach-O header.
MAGIC_BYTES = 4


def read_info_plist(app: Path) -> dict[str, object]:
    """Return the app's `Info.plist`."""
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        entries: dict[str, object] = plistlib.load(stream)
    return entries


def app_executable(app: Path) -> Path:
    """Return the app's main executable (`CFBundleExecutable` in `Contents/MacOS`).

    Raises:
        ValueError: The `Info.plist` names no executable.
    """
    name = read_info_plist(app).get("CFBundleExecutable")
    if not isinstance(name, str) or not name:
        msg = f"{app}: Info.plist names no CFBundleExecutable"
        raise ValueError(msg)
    return app / "Contents" / "MacOS" / name


def bundled_name(relative: PurePosixPath) -> str | None:
    """Return the name PyInstaller bundled a file under, from its path inside `Contents` (None: not a bundled file)."""
    parts = relative.parts
    if len(parts) < CONTENT_PATH_PARTS or parts[0] not in CONTENT_FOLDERS:
        return None
    *folders, name = parts[1:]
    restored = [folder.replace(DOT_REPLACEMENT, ".") for folder in folders]
    return PurePosixPath(*restored, name).as_posix()


def bundle_files(app: Path) -> list[PurePosixPath]:
    """Return every regular file of the app as its path inside `Contents` (links are left out: each points at one)."""
    contents_dir = app / "Contents"
    found: list[PurePosixPath] = []
    for folder, _folders, names in contents_dir.walk():
        files = [folder / name for name in names]
        regular = [path for path in files if not path.is_symlink()]
        found.extend(_relative(path, contents_dir) for path in regular)
    return sorted(found)


def _relative(path: Path, root: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(root).as_posix())


def is_mach_o(path: Path) -> bool:
    """Return whether the file is a Mach-O binary (thin or universal), by its first bytes."""
    with path.open("rb") as stream:
        header = stream.read(MACH_O_HEADER_BYTES)
    if len(header) < MACH_O_HEADER_BYTES:
        return False
    magic = int.from_bytes(header[:MAGIC_BYTES], "big")
    if magic == UNIVERSAL_MAGIC:
        return int.from_bytes(header[MAGIC_BYTES:], "big") < UNIVERSAL_ARCHITECTURES_LIMIT
    return magic in MACH_O_MAGICS
