"""The display check, run before Qt is imported (Qt's failure to load a platform plugin aborts the process).

Qt-free on purpose: the CLI imports this module to answer `display_unavailable` as JSON.
"""

import os
import sys
from collections.abc import Mapping

from transcribe_ui.gui.constants import DESKTOP_PLATFORMS, DISPLAY_VARIABLES, HEADLESS_PLATFORMS, QT_PLATFORM_VARIABLE

# Environment variables by name.
type Environ = Mapping[str, str]


def _platform_names(setting: str) -> set[str]:
    # `QT_QPA_PLATFORM` is a `;`-separated fallback list; each entry may carry `:args`.
    names: set[str] = set()
    for entry in setting.split(";"):
        name, _colon, _arguments = entry.partition(":")
        names.add(name.strip())
    return names


def display_available(environ: Environ | None = None, platform: str | None = None) -> bool:
    """Return whether Qt can open a window here (`os.environ` and `sys.platform` unless given).

    Windows and macOS always have a display. Elsewhere `QT_QPA_PLATFORM` naming a platform that
    needs no display server (`offscreen`, `minimal`, ...) is enough; otherwise at least one of
    `DISPLAY` and `WAYLAND_DISPLAY` must be set.
    """
    env = os.environ if environ is None else environ
    host = sys.platform if platform is None else platform
    if host in DESKTOP_PLATFORMS:
        return True
    chosen = _platform_names(env.get(QT_PLATFORM_VARIABLE, ""))
    if chosen & HEADLESS_PLATFORMS:
        return True
    return any(env.get(name) for name in DISPLAY_VARIABLES)
