"""The release platform of a machine: `<os>-<arch>` of the asset names (docs/ai/plans/m8.md).

Shared by `scripts/build_desktop.py` and `scripts/build_ffmpeg.py`. It must stay standard
library only and import no other script: the ffmpeg build runs it with the bare CPython of the
manylinux image, and its cache key hashes this file.
"""

import platform
import sys
from collections.abc import Mapping
from types import MappingProxyType

# `<os>` of the asset name, by `sys.platform`.
OS_NAMES: Mapping[str, str] = MappingProxyType({"win32": "windows", "darwin": "macos", "linux": "linux"})
# `<arch>` of the asset name, by `platform.machine()` in lower case.
ARCH_NAMES: Mapping[str, str] = MappingProxyType(
    {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}
)


def platform_tag(system: str, machine: str) -> str:
    """Return `<os>-<arch>` of the asset names for `sys.platform` and `platform.machine()`.

    Raises:
        ValueError: The system or the machine is not one the release builds for.
    """
    os_name = OS_NAMES.get(system)
    arch = ARCH_NAMES.get(machine.lower())
    if os_name is None or arch is None:
        msg = f"no release build for {system} on {machine}"
        raise ValueError(msg)
    return f"{os_name}-{arch}"


def host_tag() -> str:
    """Return this machine's `<os>-<arch>`.

    Raises:
        ValueError: It is not one the release builds for.
    """
    return platform_tag(sys.platform, platform.machine())
