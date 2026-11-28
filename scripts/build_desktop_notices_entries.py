"""The entries of the third-party notices (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`: from what the attribution found, the entry of each
distribution, of Python and the libraries inside it, of the bundled ffmpeg and Microsoft's runtime
libraries (`build_desktop_notices_ffmpeg.py`), of the libraries the Qt wheels vendor and of the OS
packages. A missing licence expression or file is a problem, not an exception: every
problem is collected, so the build reports them all at once. The policy check fails an entry
whose licence is the GPL (or AGPL) with neither an LGPL alternative nor a listed exemption.
Standard library only.
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from build_desktop_licences import (
    ALTERNATIVES,
    BUNDLED_LIBRARIES,
    GPL_LICENCE,
    LICENCE_OVERRIDES,
    LICENCE_SEPARATORS,
    PACKAGE_LICENCES,
    POLICY_EXEMPTIONS,
    PYTHON_EXPRESSION,
    PYTHON_LICENCES,
    QT_ATTRIBUTIONS_VERSION,
    QT_WHEELS,
    SPDX_FOLDER,
    SPDX_NAMES,
    VENDORED_LIBRARIES,
)
from build_desktop_notices_classes import (
    Attribution,
    BuildFacts,
    DistributionInfo,
    Draft,
    Interpreter,
    Notice,
    make_notice,
    normalised,
)
from build_desktop_notices_ffmpeg import ffmpeg_notices, microsoft_notice

# The names of the bundled files that belong to one library or package.
type Bundled = Sequence[str]
# OS packages (or vendored libraries) by the names of their bundled files.
type Packages = Mapping[str, Bundled]
# The problems collected so far.
type Problems = list[str]

# The trailing version number of a shared library's file name (`libicuuc.so.73`).
LIBRARY_VERSION = re.compile(r"\.so\.(\d+)")


def _is_foreign_qt(dist: DistributionInfo) -> bool:
    """Return whether a distribution is a Qt wheel of another Qt version than the committed attributions."""
    own_version = dist.version.startswith(f"{QT_ATTRIBUTIONS_VERSION}.")
    return dist.key in QT_WHEELS and not own_version


def distribution_notice(dist: DistributionInfo, licence_dir: Path, problems: Problems) -> Notice:
    """Return the entry of a distribution, with its override (`LICENCE_OVERRIDES`) applied.

    A declared licence file the distribution lacks is a problem; so is a Qt wheel of another Qt
    version than the committed attributions of Qt's third-party code describe.
    """
    missing = (f"{dist.name}: the declared licence file {name} is missing" for name in dist.missing_licences)
    problems.extend(missing)
    qt_version = f"Qt {QT_ATTRIBUTIONS_VERSION}"
    if _is_foreign_qt(dist):
        what = f"{dist.name} {dist.version}: the attributions of Qt's third-party code are of {qt_version}"
        problems.append(f"{what} (run scripts/build_qt_attributions.py)")
    override = LICENCE_OVERRIDES.get(dist.key)
    expression = dist.expression if override is None else override.expression
    files = dist.licences
    if override is not None and override.licences:
        files = tuple(licence_dir / name for name in override.licences)
    return make_notice(Draft(dist.name, dist.version, expression, files), problems)


def python_notice(interpreter: Interpreter, licence_dir: Path, problems: Problems) -> Notice:
    """Return Python's entry: its licence and the licences of the software CPython incorporates."""
    own = () if interpreter.licence is None else (interpreter.licence,)
    files = (*own, *(licence_dir / name for name in PYTHON_LICENCES))
    note = (
        "The interpreter is a python-build-standalone build (https://github.com/astral-sh/python-build-standalone). "
        "The libraries it links are listed as their own entries."
    )
    return make_notice(Draft("Python", interpreter.version, PYTHON_EXPRESSION, files, note), problems)


def library_notices(names: set[str], facts: BuildFacts, problems: Problems) -> list[Notice]:
    """Return the entries of the interpreter's bundled libraries, in the table's order."""
    notices = []
    fallback = f"bundled with Python {facts.interpreter.version}"
    note = "Part of the Python interpreter in this executable."
    for library in BUNDLED_LIBRARIES:
        if library.name in names:
            version = facts.interpreter.library_versions.get(library.name, fallback)
            files = (facts.licence_dir / library.licence,)
            draft = Draft(library.name, version, library.expression, files, note)
            notices.append(make_notice(draft, problems))
    return notices


def _package_notice(name: str, bundled: Bundled, facts: BuildFacts, problems: Problems) -> Notice | None:
    """Return the entry of an OS package, or None (with a problem) when the package database does not describe it."""
    package = None if facts.system is None else facts.system.describe(name)
    if package is None:
        why = "" if facts.system is None else facts.system.last_failure()
        listed = ", ".join(bundled)
        problems.append(f"{name}: the OS package of {listed} is not described by the package database ({why})")
        return None
    libraries = ", ".join(sorted(set(bundled)))
    lines = [f"Shared libraries of the build system's package {name}: {libraries}."]
    expression = package.expression
    corrected = PACKAGE_LICENCES.get(name)
    if corrected is not None:
        lines.append(f"The package states {expression}; {corrected.reason}.")
        expression = corrected.expression
    licences = package.licences or _standard_texts(expression, facts.licence_dir)
    if licences and not package.licences:
        lines.append("The package ships no licence file: the standard text of its licence follows.")
    lines.extend(line for line in (package.source,) if line)
    note = "\n".join(lines)
    return make_notice(Draft(name, package.version, expression, licences, note), problems)


def package_notices(packages: Packages, facts: BuildFacts, problems: Problems) -> list[Notice]:
    """Return the entries of the OS packages whose shared libraries the executable contains."""
    notices = []
    for name, files in sorted(packages.items()):
        notice = _package_notice(name, files, facts, problems)
        if notice is not None:
            notices.append(notice)
    return notices


def _standard_texts(expression: str, licence_dir: Path) -> tuple[Path, ...]:
    """Return the committed standard texts of every licence an expression names, or () when one has none."""
    parts = LICENCE_SEPARATORS.split(expression)
    names = [part.strip() for part in parts if part and part.strip()]
    identifiers = [SPDX_NAMES.get(name) for name in names]
    if not identifiers or None in identifiers:
        return ()
    unique = dict.fromkeys(identifier for identifier in identifiers if identifier)
    spdx_dir = licence_dir / SPDX_FOLDER
    return tuple(spdx_dir / f"{identifier}.txt" for identifier in unique)


def vendored_notices(vendored: Packages, facts: BuildFacts, problems: Problems) -> list[Notice]:
    """Return the entries of the libraries a wheel vendors besides its own code (ICU, Mesa's llvmpipe)."""
    notices = []
    for library in VENDORED_LIBRARIES:
        bundled = sorted(set(vendored.get(library.name, ())))
        if bundled:
            matches = (LIBRARY_VERSION.search(name) for name in bundled)
            versions = {match.group(1) for match in matches if match}
            version = ", ".join(sorted(versions)) or "as shipped with Qt for Python"
            files = tuple(facts.licence_dir / name for name in library.licences)
            note = f"{library.note} Files: {', '.join(bundled)}."
            draft = Draft(library.name, version, library.expression, files, note)
            notices.append(make_notice(draft, problems))
    return notices


def policy_problems(notices: Sequence[Notice]) -> list[str]:
    """Return the entries whose licence is the GPL or AGPL with neither an LGPL alternative nor a listed exemption."""
    problems = []
    for notice in notices:
        if normalised(notice.name) in POLICY_EXEMPTIONS:
            continue
        alternatives = ALTERNATIVES.split(notice.expression)
        if all(GPL_LICENCE.search(alternative) for alternative in alternatives):
            problems.append(
                f"{notice.name}: its licence ({notice.expression}) is the GPL without an LGPL alternative or a listed "
                "exemption (build_desktop_licences.POLICY_EXEMPTIONS)",
            )
    return problems


def _distribution_notices(found: Attribution, facts: BuildFacts, problems: Problems) -> list[Notice]:
    """Return the entries of the attributed distributions and of PyInstaller's (a problem when it is not installed)."""
    licence_dir = facts.licence_dir
    notices = [distribution_notice(dist, licence_dir, problems) for dist in found.distributions.values()]
    if facts.pyinstaller is None:
        problems.append("PyInstaller's distribution is not installed")
    elif facts.pyinstaller.key not in found.distributions:
        notices.append(distribution_notice(facts.pyinstaller, licence_dir, problems))
    return notices


def _bundled_notices(found: Attribution, facts: BuildFacts, problems: Problems) -> list[Notice]:
    """Return the entries of Python, ffmpeg and Microsoft's runtime, as far as the executable contains them."""
    notices = []
    if found.python or found.libraries:
        notices.append(python_notice(facts.interpreter, facts.licence_dir, problems))
    notices.extend(library_notices(found.libraries, facts, problems))
    if found.ffmpeg:
        notices.extend(ffmpeg_notices(facts, problems))
    if found.microsoft:
        notices.append(microsoft_notice(facts, problems))
    return notices


def build_notices(found: Attribution, facts: BuildFacts) -> tuple[list[Notice], list[str]]:
    """Return every entry of the notices and the problems (the attribution's included)."""
    problems = list(found.problems)
    notices = _distribution_notices(found, facts, problems)
    notices.extend(_bundled_notices(found, facts, problems))
    notices.extend(vendored_notices(found.vendored, facts, problems))
    notices.extend(package_notices(found.packages, facts, problems))
    problems.extend(policy_problems(notices))
    return notices, problems
