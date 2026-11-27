"""How the host's file system compares names: the path-flavour rule (architecture §3.2).

Injected through the ports, so the Windows and macOS rules are tested on any OS. Reported paths
are always kept as the OS returned them; the rule only shapes comparisons (sidecar stems,
selection paths).
"""

import sys
import unicodedata
from dataclasses import dataclass

from transcribe_contracts.classes import PathFlavor


@dataclass(frozen=True)
class PathRules:
    """Name comparison and path splitting of one file-system family."""

    flavor: PathFlavor
    # Names differing only in case are the same file (Windows, default macOS volumes).
    fold_case: bool
    # Names differing only in Unicode normalisation are the same file (macOS).
    compose_unicode: bool
    # Characters separating path parts; the first one is the canonical separator.
    separators: tuple[str, ...]

    def key(self, name: str) -> str:
        """Return the form under which this file system considers names equal."""
        if self.compose_unicode:
            name = unicodedata.normalize("NFC", name)
        return name.casefold() if self.fold_case else name

    def parts(self, path: str) -> tuple[str, ...]:
        """Split a path into its parts; empty and `.` parts are dropped, `..` is kept."""
        canonical = self.separators[0]
        for separator in self.separators[1:]:
            path = path.replace(separator, canonical)
        pieces = path.split(canonical)
        return tuple(part for part in pieces if part not in {"", "."})


def windows_rules() -> PathRules:
    """Return the Windows rule: case-insensitive, backslash and slash as separators."""
    return PathRules(flavor="windows", fold_case=True, compose_unicode=False, separators=("\\", "/"))


def macos_rules() -> PathRules:
    """Return the macOS rule: case- and normalisation-insensitive (APFS and HFS+ defaults)."""
    return PathRules(flavor="posix", fold_case=True, compose_unicode=True, separators=("/",))


def posix_rules() -> PathRules:
    """Return the rule of other POSIX systems: names compared exactly."""
    return PathRules(flavor="posix", fold_case=False, compose_unicode=False, separators=("/",))


def host_rules() -> PathRules:
    """Return the rule of the running OS."""
    if sys.platform == "win32":
        return windows_rules()
    if sys.platform == "darwin":
        return macos_rules()
    return posix_rules()


def display_order(name: str) -> tuple[str, str]:
    """Return the sort key of a name: the same order on every OS (composed, case-folded, then exact)."""
    return unicodedata.normalize("NFC", name).casefold(), name
