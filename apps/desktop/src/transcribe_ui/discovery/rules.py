"""How the host's file system compares names: the path-flavour rule (architecture §3.2).

Injected through the ports, so the Windows and macOS rules are tested on any OS. Reported paths
are always kept as the OS returned them; the rule only shapes comparisons (sidecar stems,
selection paths).
"""

import sys
import unicodedata
from dataclasses import dataclass
from typing import Literal

from transcribe_contracts.classes import PathFlavor

# How a file system compares names that differ only in case.
type CaseRule = Literal["exact", "simple", "full"]


def _simple_upper(character: str) -> str:
    upper = character.upper()
    return upper if len(upper) == 1 else character


@dataclass(frozen=True)
class PathRules:
    """Name comparison and path splitting of one file-system family."""

    flavor: PathFlavor
    # How names differing only in case compare: exactly, by simple one-to-one upper-casing per
    # character (as Windows' upper-case table does: `ß` stays `ß`), or by full Unicode case folding (macOS).
    case_rule: CaseRule
    # Names differing only in Unicode normalisation are the same file (macOS).
    compose_unicode: bool
    # Characters separating path parts; the first one is the canonical separator.
    separators: tuple[str, ...]

    def key(self, name: str) -> str:
        """Return the form under which this file system considers names equal."""
        if self.compose_unicode:
            name = unicodedata.normalize("NFC", name)
        if self.case_rule == "simple":
            return "".join(map(_simple_upper, name))
        return name.casefold() if self.case_rule == "full" else name

    def parts(self, path: str) -> tuple[str, ...]:
        """Split a path into its parts; empty and `.` parts are dropped, `..` is kept."""
        canonical = self.separators[0]
        for separator in self.separators[1:]:
            path = path.replace(separator, canonical)
        pieces = path.split(canonical)
        return tuple(part for part in pieces if part not in {"", "."})


def windows_rules() -> PathRules:
    """Return the Windows rule: case-insensitive (simple upper-casing), backslash and slash as separators."""
    return PathRules(flavor="windows", case_rule="simple", compose_unicode=False, separators=("\\", "/"))


def macos_rules() -> PathRules:
    """Return the macOS rule: case- and normalisation-insensitive (APFS and HFS+ defaults)."""
    return PathRules(flavor="posix", case_rule="full", compose_unicode=True, separators=("/",))


def posix_rules() -> PathRules:
    """Return the rule of other POSIX systems: names compared exactly."""
    return PathRules(flavor="posix", case_rule="exact", compose_unicode=False, separators=("/",))


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
