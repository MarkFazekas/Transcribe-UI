"""The facts of the Python interpreter the third-party notices are made from (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_facts.py`, which runs inside the PyInstaller process of a variant:
the interpreter's prefix, licence file, built-in modules and the versions of the libraries it
reports (OpenSSL, SQLite, zlib, Expat, mpdecimal, ncurses). Standard library only.
"""

import importlib
import platform
import sys
import sysconfig
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from types import MappingProxyType

from build_desktop_notices_classes import Interpreter

# Reads the text of one library version from what the interpreter reports.
type Reader = Callable[[object], str | None]
# Name of Python's licence file.
PYTHON_LICENCE_FILE = "LICENSE.txt"


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
