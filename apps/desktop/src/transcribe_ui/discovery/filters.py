"""Selection filters: media kind, sidecar subtitle languages, name (architecture §3.2).

Filters see only what the scan found, so the same request selects the same files on every
machine. Names are compared without their extension, composed (NFC) and case-folded; a regex is
searched in the composed name, ignoring case.
"""

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import PurePath

from transcribe_contracts.classes import MediaKind
from transcribe_ui.discovery.classes import NameFilter, NameMatch, ScanFile, SelectionFilter, SubtitleRule


@dataclass(frozen=True)
class FileFacts:
    """What the filters look at in one scanned file."""

    kind: MediaKind
    # Name without the extension, composed (NFC).
    stem: str
    # `stem` case-folded.
    folded_stem: str
    # Languages of its sidecar subtitles that are not forced.
    languages: frozenset[str]


# Decides whether a file matches.
type FilePredicate = Callable[[FileFacts], bool]


def _composed(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def facts_of(scanned: ScanFile) -> FileFacts:
    """Return what the filters need of a scanned file."""
    stem = _composed(PurePath(scanned.name).stem)
    languages = frozenset(
        sidecar.language for sidecar in scanned.subtitles if sidecar.language is not None and not sidecar.forced
    )
    folded = stem.casefold()
    return FileFacts(kind=scanned.kind, stem=stem, folded_stem=folded, languages=languages)


def _kind_is(kind: str, facts: FileFacts) -> bool:
    return facts.kind == kind


def _subtitles_match(wanted: frozenset[str], rule: SubtitleRule, facts: FileFacts) -> bool:
    present = wanted & facts.languages
    if rule == "all":
        return present == wanted
    if rule == "any":
        return bool(present)
    return present != wanted


def _text_matches(match: NameMatch, text: str, facts: FileFacts) -> bool:
    stem = facts.folded_stem
    if match == "starts_with":
        return stem.startswith(text)
    if match == "ends_with":
        return stem.endswith(text)
    return text in stem


def _regex_matches(pattern: re.Pattern[str], facts: FileFacts) -> bool:
    return pattern.search(facts.stem) is not None


def _name_check(name: NameFilter) -> FilePredicate:
    if name.match == "regex":
        return partial(_regex_matches, re.compile(_composed(name.text), re.IGNORECASE))
    return partial(_text_matches, name.match, _composed(name.text).casefold())


def _all_pass(checks: Sequence[FilePredicate], facts: FileFacts) -> bool:
    return all(check(facts) for check in checks)


def compile_filter(filters: SelectionFilter) -> FilePredicate:
    """Return the predicate of a filter; an empty filter matches every file."""
    checks: list[FilePredicate] = []
    if filters.media_kind != "any":
        checks.append(partial(_kind_is, filters.media_kind))
    subtitles = filters.subtitles
    if subtitles is not None:
        wanted = frozenset(subtitles.languages)
        checks.append(partial(_subtitles_match, wanted, subtitles.rule))
    if filters.name is not None:
        checks.append(_name_check(filters.name))
    return partial(_all_pass, tuple(checks))
