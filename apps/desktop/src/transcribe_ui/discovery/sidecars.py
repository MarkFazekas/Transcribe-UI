"""Sidecar subtitles: which media file of a folder a subtitle file belongs to (architecture §3.2).

A subtitle belongs to the media files whose stem is the longest `.`-bounded prefix of its name
(`Movie.part2.en.srt` belongs to `Movie.part2.mkv` rather than `Movie.mkv`); media files that
share that stem (`Movie.mkv`, `Movie.mp4`) all get it. The rest of the name gives the language
and the forced flag. Stems compare under the host's path-flavour rule.
"""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePath

from transcribe_ui.discovery.classes import Sidecar
from transcribe_ui.discovery.languages import parse_tokens
from transcribe_ui.discovery.rules import PathRules

# Stem key -> names of the media files with that stem.
type StemIndex = Mapping[str, Sequence[str]]
# A file name and the full path of the file.
type NamedPath = tuple[str, str]
# Media file name -> its sidecar subtitles.
type SidecarsByMedia = dict[str, list[Sidecar]]
# The media files a subtitle belongs to, and the name tokens after their stem.
type Owners = tuple[Sequence[str], list[str]]


def stem_index(media_names: Iterable[str], rules: PathRules) -> StemIndex:
    """Return the media files of a folder grouped by the rule key of their stem."""
    index: dict[str, list[str]] = {}
    for name in media_names:
        stem = PurePath(name).stem
        index.setdefault(rules.key(stem), []).append(name)
    return index


def _owners(base: str, stems: StemIndex, rules: PathRules) -> Owners | None:
    parts = base.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        owners = stems.get(rules.key(candidate))
        if owners:
            return owners, parts[cut:]
    return None


def _sidecar(name: str, path: str, tokens: Sequence[str]) -> Sidecar:
    language, forced = parse_tokens(tokens)
    extension = PurePath(name).suffix[1:].lower()
    return Sidecar(name=name, path=path, extension=extension, language=language, forced=forced)


def match_sidecars(subtitles: Iterable[NamedPath], stems: StemIndex, rules: PathRules) -> SidecarsByMedia:
    """Return media file name -> its sidecar subtitles; subtitles of no media file are dropped."""
    found: SidecarsByMedia = {}
    for name, path in subtitles:
        match = _owners(PurePath(name).stem, stems, rules)
        if match is None:
            continue
        owners, tokens = match
        sidecar = _sidecar(name, path, tokens)
        for owner in owners:
            found.setdefault(owner, []).append(sidecar)
    return found
