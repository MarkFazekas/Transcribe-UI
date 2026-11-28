"""Write the attributions of the third-party code inside Qt's shipped modules (docs/ai/plans/m8.md, M8.3).

Usage, from the repository root, with Qt's source packages of the bundled version unpacked
(qtbase, qtsvg and qtimageformats, from https://download.qt.io/official_releases/qt/):

    uv run --no-project scripts/build_qt_attributions.py <version> <unpacked submodule> ...

Qt documents each third-party component in a `qt_attribution.json` next to its code. This
script writes every component except those of the build system, tests and modules the app does
not ship (Android, WebAssembly, the SQLite plugin) to `apps/desktop/packaging/licences/
qt-third-party.txt`: name, version, licence, copyright, where Qt uses it, and the licence text
(each text once; later uses refer to it). The notices check that the file's Qt version is the
bundled one, so a Qt upgrade means running this again. Standard library only.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from build_desktop_licences import LICENCES_DIR, QT_ATTRIBUTIONS

# One component an attribution file describes.
type Component = dict[str, object]
# The first component of each licence text (by digest), for referring to repeated texts.
type Seen = dict[str, str]
# Name of Qt's attribution files.
ATTRIBUTION_FILE = "qt_attribution.json"
# Folders (relative to a submodule) of components that are never part of the shipped modules.
EXCLUDED_FOLDERS = (
    "cmake",
    "tests",
    "util",
    "src/testlib",
    "src/testinternal",
    "src/3rdparty/android",
    "src/3rdparty/gradle",
    "src/3rdparty/wasm",
    "src/3rdparty/sqlite",
)
# Components outside an excluded folder that the shipped modules still contain (QGradient's presets).
KEPT_IDS = frozenset(("webgradients",))
# Folder of the licence texts of a submodule, named by SPDX identifier.
SPDX_FOLDER = "LICENSES"
# Encoding of the files read and written.
ENCODING = "utf-8"
# Width of the line between two components.
RULE_WIDTH = 78
# The line between two components.
RULE = "-" * RULE_WIDTH


def _components(attribution: Path) -> list[Component]:
    """Return the components one attribution file describes (an object or a list of them)."""
    parsed = json.loads(attribution.read_text(encoding=ENCODING), strict=False)
    listed = parsed if isinstance(parsed, list) else [parsed]
    return [component for component in listed if isinstance(component, dict)]


def _is_shipped(attribution: Path, submodule: Path, component: Component) -> bool:
    folder = attribution.parent.relative_to(submodule).as_posix()
    below = tuple(f"{prefix}/" for prefix in EXCLUDED_FOLDERS)
    excluded = folder in EXCLUDED_FOLDERS or folder.startswith(below)
    return component.get("Id") in KEPT_IDS or not excluded


def _named_files(component: Component) -> list[str]:
    """Return the licence file names a component names (one name or a list)."""
    named = component.get("LicenseFiles") or component.get("LicenseFile")
    if isinstance(named, list):
        return [str(name) for name in named]
    return [named] if isinstance(named, str) else []


def _licence_files(attribution: Path, submodule: Path, component: Component) -> list[Path]:
    """Return the component's licence files: those it names, else the submodule's SPDX texts of its licence."""
    names = _named_files(component)
    if names:
        return [attribution.parent / name for name in names]
    licence_id = str(component.get("LicenseId", ""))
    identifiers = licence_id.replace("(", " ").replace(")", " ").split()
    spdx_folder = submodule / SPDX_FOLDER
    candidates = [spdx_folder / f"{identifier}.txt" for identifier in identifiers]
    return [candidate for candidate in candidates if candidate.is_file()]


def _copyright(component: Component) -> str:
    holders = component.get("Copyright", "")
    if isinstance(holders, list):
        return "\n".join(str(holder) for holder in holders)
    return str(holders)


def _title(component: Component) -> str:
    """Return the component's name and version."""
    name = component.get("Name", component.get("Id"))
    return f"{name} {component.get('Version', '')}".strip()


def _text_or_reference(text: str, digest: str, seen: Seen) -> str:
    """Return the text, or a reference to the component that had the same text before."""
    earlier = seen.get(digest)
    return text if earlier is None else f"(The same text as for {earlier} above.)"


def _entry(component: Component, files: Sequence[Path], seen: Seen) -> Iterator[str]:
    name = _title(component)
    licence = component.get("LicenseId", component.get("License", ""))
    yield from ("", RULE, name, f"Licence: {licence}")
    usage = component.get("QtUsage", "")
    copyright_text = _copyright(component)
    yield from (f"Used: {usage}", f"Copyright:\n{copyright_text}")
    for path in files:
        text = path.read_text(encoding=ENCODING, errors="replace").strip("\n")
        digest = hashlib.sha256(text.encode(ENCODING)).hexdigest()
        yield f"\n--- {path.name} ---"
        yield _text_or_reference(text, digest, seen)
        seen.setdefault(digest, name)


def attributions(version: str, submodules: Sequence[Path]) -> str:
    """Return the attributions text of the shipped third-party components of the submodules."""
    lines = [
        f"Third-party components inside Qt {version} (qtbase, qtsvg, qtimageformats), as Qt's",
        "qt_attribution.json files describe them. Build-system, test, Android, WebAssembly and",
        "SQL components are left out: the app's executables do not contain them.",
    ]
    seen: Seen = {}
    for submodule in submodules:
        for attribution in sorted(submodule.rglob(ATTRIBUTION_FILE)):
            for component in _components(attribution):
                if _is_shipped(attribution, submodule, component):
                    files = _licence_files(attribution, submodule, component)
                    lines.extend(_entry(component, files, seen))
    return "\n".join([*lines, ""])


def main(argv: Sequence[str] | None = None) -> int:
    """Write the attributions file; return the exit code."""
    parser = argparse.ArgumentParser(description="Write the attributions of Qt's third-party components.")
    parser.add_argument("version", help="the Qt version of the sources (the bundled PySide6's)")
    parser.add_argument("submodules", nargs="+", type=Path, help="unpacked qtbase, qtsvg and qtimageformats sources")
    args = parser.parse_args(argv)
    text = attributions(args.version, args.submodules)
    (LICENCES_DIR / QT_ATTRIBUTIONS).write_text(text, encoding=ENCODING, newline="\n")
    sys.stdout.write(f"Wrote {LICENCES_DIR / QT_ATTRIBUTIONS}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
