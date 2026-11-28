"""The installed Python distributions the third-party notices name (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_facts.py`, which runs inside the PyInstaller process of a variant:
reads each distribution's metadata (name, version, licence expression, `License-File` entries or
licence-like files of its metadata folder) and indexes the distributions by the files they
installed and by normalised name. Standard library only.
"""

from collections.abc import Iterable, Mapping
from importlib.metadata import Distribution, PackagePath
from pathlib import Path

from build_desktop_notices import file_key
from build_desktop_notices_classes import DistributionInfo

# The licence files found and the declared names not found.
type Declared = tuple[list[Path], list[str]]
# The licence files of a distribution and the declared ones it lacks.
type LicencePaths = tuple[tuple[Path, ...], tuple[str, ...]]
# The distributions by the key of each file they installed, and by normalised name.
type DistributionIndex = tuple[dict[str, DistributionInfo], dict[str, DistributionInfo]]

# Longest `License` metadata field taken as an expression (longer ones are licence texts).
LICENSE_FIELD_LIMIT = 120
# Prefix of a licence classifier (its last part names the licence).
LICENCE_CLASSIFIER = "License :: "
# Names of licence files a distribution without `License-File` metadata may have in its metadata folder.
LICENCE_NAMES = ("LICEN", "COPYING", "NOTICE")
# The name of the metadata folder of an installed distribution (its suffix).
METADATA_SUFFIX = ".dist-info"
# Separator of the classifier's parts.
CLASSIFIER_SEPARATOR = " :: "
# What old packaging tools wrote into a metadata field they had no value for.
UNKNOWN = "UNKNOWN"


def _text_of(metadata: Mapping[str, object], key: str) -> str:
    """Return a metadata field as a stripped text ("" when it is missing, no text or `UNKNOWN`)."""
    found = metadata.get(key)
    text = found.strip() if isinstance(found, str) else ""
    return "" if text.upper() == UNKNOWN else text


def _located(dist: Distribution, path: PackagePath) -> Path:
    return Path(str(dist.locate_file(path)))


def licence_expression(metadata: Mapping[str, object], classifiers: list[str]) -> str:
    """Return the licence expression: `License-Expression`, else a short `License`, else the licence classifiers."""
    expression = _text_of(metadata, "License-Expression")
    if expression:
        return expression
    field = _text_of(metadata, "License")
    if field and "\n" not in field and len(field) <= LICENSE_FIELD_LIMIT:
        return field
    licences = [name for name in classifiers if name.startswith(LICENCE_CLASSIFIER)]
    parts = (name.rsplit(CLASSIFIER_SEPARATOR, 1) for name in licences)
    return ", ".join(part[-1] for part in parts)


def _in_metadata_folder(path: PackagePath) -> bool:
    return bool(path.parts) and path.parts[0].endswith(METADATA_SUFFIX)


def _inner(path: PackagePath) -> str:
    """Return a file's path inside its metadata folder."""
    return "/".join(path.parts[1:])


def _declared(dist: Distribution, files: list[PackagePath]) -> Declared:
    """Return the files of the metadata folder that the `License-File` fields name, and the names not found."""
    found: list[Path] = []
    missing: list[str] = []
    for name in dist.metadata.get_all("License-File") or []:
        wanted = name.replace("\\", "/")
        accepted = {wanted, f"licenses/{wanted}"}
        matches = [path for path in files if _inner(path) in accepted]
        if matches:
            found.append(_located(dist, matches[0]))
        else:
            missing.append(name)
    return found, missing


def licence_paths(dist: Distribution) -> LicencePaths:
    """Return a distribution's licence files and the declared ones it lacks.

    The files are those the `License-File` fields name, else the licence-like files of the
    metadata folder.
    """
    files = [path for path in dist.files or () if _in_metadata_folder(path)]
    if dist.metadata.get_all("License-File"):
        found, missing = _declared(dist, files)
        return tuple(found), tuple(missing)
    like = [path for path in files if path.name.upper().startswith(LICENCE_NAMES)]
    return tuple(_located(dist, path) for path in like), ()


def describe_distribution(dist: Distribution) -> DistributionInfo:
    """Return a distribution's name, version, licence expression and licence files."""
    metadata = dist.metadata
    classifiers = metadata.get_all("Classifier") or []
    fields = {key: metadata.get(key) for key in ("License-Expression", "License")}
    expression = licence_expression(fields, classifiers)
    found, missing = licence_paths(dist)
    return DistributionInfo(metadata["Name"], dist.version, expression, found, missing)


def distribution_index(installed: Iterable[Distribution]) -> DistributionIndex:
    """Return the distributions by the key of each file they installed, and by normalised name (the first one found wins)."""
    by_file: dict[str, DistributionInfo] = {}
    by_name: dict[str, DistributionInfo] = {}
    for dist in installed:
        described = describe_distribution(dist)
        if described.key in by_name:
            continue
        by_name[described.key] = described
        for path in dist.files or ():
            by_file.setdefault(file_key(_located(dist, path)), described)
    return by_file, by_name
