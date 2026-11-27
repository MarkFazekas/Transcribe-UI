"""The display check, run before Qt is imported (Qt's failure to load a platform plugin aborts the process).

Qt-free on purpose: `gui/main.py` runs it before importing Qt, to answer `display_unavailable`
as JSON instead of crashing.

What it cannot see, so Qt can still abort (M8 documents them for Linux users and its smoke run
starts the build offscreen): a `DISPLAY` or `WAYLAND_DISPLAY` naming a server that is gone or
refuses the connection (a stale SSH forwarding, another user's session), and a platform plugin
whose system libraries are missing (xcb needs `libxcb-cursor0` and friends).
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from transcribe_ui.gui.constants import (
    DEFAULT_WAYLAND_SOCKET,
    DESKTOP_PLATFORMS,
    DISPLAY_VARIABLES,
    FORCED_PLATFORM_MESSAGE,
    HEADLESS_PLATFORMS,
    NO_DISPLAY_MESSAGE,
    QT_PLATFORM_VARIABLE,
    RUNTIME_DIR_VARIABLE,
    WAYLAND_DISPLAY_VARIABLE,
    WAYLAND_PLATFORM_PREFIX,
    WAYLAND_SOCKET_VARIABLE,
    X11_DISPLAY_VARIABLE,
    X11_PLATFORM,
)

# Environment variables by name.
type Environ = Mapping[str, str]


def _platform_names(setting: str) -> list[str]:
    # `QT_QPA_PLATFORM` is a `;`-separated fallback list; each entry may carry `:args`.
    names: list[str] = []
    for entry in setting.split(";"):
        name, _colon, _arguments = entry.partition(":")
        if name.strip():
            names.append(name.strip())
    return names


def _can_start(name: str, env: Environ) -> bool:
    """Return whether the named platform plugin finds what it needs (False when the check cannot tell)."""
    if name in HEADLESS_PLATFORMS:
        return True
    if name == X11_PLATFORM:
        return bool(env.get(X11_DISPLAY_VARIABLE))
    if name.startswith(WAYLAND_PLATFORM_PREFIX):
        return _wayland_reachable(env)
    # Device platforms (eglfs, linuxfb, minimalegl, vkkhrdisplay, ...) depend on hardware.
    return False


def _wayland_reachable(env: Environ) -> bool:
    """Return whether a Wayland client finds a compositor socket the way libwayland looks for one.

    Qt passes no name to `wl_display_connect`, so libwayland takes `WAYLAND_SOCKET`, else
    `WAYLAND_DISPLAY`, else `wayland-0` in `XDG_RUNTIME_DIR`.
    """
    if env.get(WAYLAND_SOCKET_VARIABLE) or env.get(WAYLAND_DISPLAY_VARIABLE):
        return True
    runtime_dir = env.get(RUNTIME_DIR_VARIABLE, "")
    return bool(runtime_dir) and (Path(runtime_dir) / DEFAULT_WAYLAND_SOCKET).exists()


def display_problem(environ: Environ | None = None, platform: str | None = None) -> str | None:
    """Return why Qt cannot open a window here, or None if it can (`os.environ`, `sys.platform` unless given).

    Windows and macOS always have a display. Elsewhere, a `QT_QPA_PLATFORM` list needs one entry
    that can start: `offscreen`, `minimal` or `vnc` always; `xcb` with `DISPLAY`; `wayland*`
    with a compositor socket (`_wayland_reachable`); device platforms (`eglfs`, `linuxfb`, ...) never count. Without
    `QT_QPA_PLATFORM`, Qt picks xcb or Wayland, so one of `DISPLAY` and `WAYLAND_DISPLAY` must be set.
    """
    env = os.environ if environ is None else environ
    host = sys.platform if platform is None else platform
    if host in DESKTOP_PLATFORMS:
        return None
    forced = env.get(QT_PLATFORM_VARIABLE, "")
    chosen = _platform_names(forced)
    if chosen:
        if any(_can_start(name, env) for name in chosen):
            return None
        return FORCED_PLATFORM_MESSAGE.format(platforms=forced)
    if any(env.get(name) for name in DISPLAY_VARIABLES):
        return None
    return NO_DISPLAY_MESSAGE


def display_available(environ: Environ | None = None, platform: str | None = None) -> bool:
    """Return whether Qt can open a window here (see `display_problem`)."""
    return display_problem(environ, platform) is None
