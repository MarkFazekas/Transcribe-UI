"""Translation catalogs of the desktop app: refresh them, or check that they are current.

Usage (from anywhere in the repo): `uv run scripts/catalogs.py update` or `... check`.

`update` extracts the texts marked with `tr` and `ntr` (mapping in `apps/desktop/babel.cfg`)
into `transcribe_ui/locale/transcribe_ui.pot`, merges it into the `en` and `hu` catalogs (new
texts empty, removed ones dropped, no fuzzy matching) and compiles the `.mo` files the app reads.
A file whose content would not change apart from its creation date is not rewritten.

`check` (run by the desktop target of `scripts/check.py`) fails when any of that would change a
file, when a Hungarian text is missing or fuzzy, or when a Hungarian text does not use exactly
the `{fields}` of its English text, format specs included. Exit code 1 on a failed check.
"""

import argparse
import configparser
import io
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from babel.messages.catalog import Catalog, Message
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP = REPO_ROOT / "apps" / "desktop"
MAPPING_FILE = DESKTOP / "babel.cfg"
LOCALE_DIR = DESKTOP / "src" / "transcribe_ui" / "locale"
DOMAIN = "transcribe_ui"
TEMPLATE = LOCALE_DIR / f"{DOMAIN}.pot"
PROJECT = "Transcribe-UI"
# Comment above the template's header.
TEMPLATE_COMMENT = "# Texts of Transcribe-UI to translate, extracted by scripts/catalogs.py.\n#"
# Languages with a catalog; the first one is the msgids' language (its texts may stay empty).
LANGUAGES = ("en", "hu")
# Languages whose every text must be translated.
TRANSLATED_LANGUAGES = frozenset(("hu",))
# The marking functions: `tr(text)` and `ntr(singular, plural, count)`.
KEYWORDS = MappingProxyType({"tr": None, "ntr": (1, 2)})
# Separator of a mapping section's extraction method and file pattern (`[python: <pattern>]`).
SECTION_SEPARATOR = ":"
# Header line that changes at every extraction; files are compared without it.
CREATION_DATE = re.compile(rb"^\"POT-Creation-Date: [^\"]*\"\n", re.MULTILINE)
# A `{field}` of a text, with its format spec (`{cost:.2f}`, `{audio_s:duration}`) compared too.
FIELD = re.compile(r"\{\w+[^{}]*\}")
# What `check` reports for a file `update` would change.
NOT_CURRENT = "not current (run `uv run scripts/catalogs.py update`)"

# A message's text: one string, the forms of a plural, or none.
type Texts = str | tuple[str, ...] | list[str] | None


@dataclass(frozen=True)
class Planned:
    """A file and the bytes `update` would write to it."""

    path: Path
    rendered: bytes


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def method_map() -> list[tuple[str, str]]:
    """Return the file patterns of `babel.cfg` with their extraction methods (`[method: pattern]`)."""
    mapping = configparser.ConfigParser()
    mapping.read(MAPPING_FILE, encoding="utf-8")
    sections = (section.split(SECTION_SEPARATOR, maxsplit=1) for section in mapping.sections())
    return [(pattern.strip(), method.strip()) for method, pattern in sections]


def extract() -> Catalog:
    """Return the template: every marked text of the app's code, with the files it is in."""
    template = Catalog(project=PROJECT, domain=DOMAIN, header_comment=TEMPLATE_COMMENT)
    found = extract_from_dir(DESKTOP, method_map(), keywords=KEYWORDS, strip_comment_tags=True)
    for filename, lineno, message, comments, context in found:
        location = (Path(filename).as_posix(), lineno)
        template.add(message, None, [location], auto_comments=comments, context=context)
    return template


def _po_bytes(catalog: Catalog) -> bytes:
    buffer = io.BytesIO()
    write_po(buffer, catalog, sort_output=True, include_lineno=False, ignore_obsolete=True)
    return buffer.getvalue()


def _mo_bytes(catalog: Catalog) -> bytes:
    buffer = io.BytesIO()
    write_mo(buffer, catalog)
    return buffer.getvalue()


def _catalog_file(language: str, suffix: str) -> Path:
    return LOCALE_DIR / language / "LC_MESSAGES" / f"{DOMAIN}{suffix}"


def _merged(language: str, template: Catalog) -> Catalog:
    """Return the language's catalog updated to the template (a new one if it has none yet)."""
    po_path = _catalog_file(language, ".po")
    if po_path.is_file():
        with po_path.open("rb") as stored:
            catalog = read_po(stored, locale=language, domain=DOMAIN)
    else:
        catalog = Catalog(locale=language, domain=DOMAIN, project=PROJECT)
    catalog.update(template, no_fuzzy_matching=True, update_creation_date=False)
    return catalog


def plan(template: Catalog) -> tuple[list[Planned], list[Catalog]]:
    """Return every file `update` writes with its bytes, and the merged catalogs."""
    planned = [Planned(TEMPLATE, _po_bytes(template))]
    catalogs = []
    for language in LANGUAGES:
        catalog = _merged(language, template)
        catalogs.append(catalog)
        planned.append(Planned(_catalog_file(language, ".po"), _po_bytes(catalog)))
        planned.append(Planned(_catalog_file(language, ".mo"), _mo_bytes(catalog)))
    return planned, catalogs


def _without_date(rendered: bytes) -> bytes:
    return CREATION_DATE.sub(b"", rendered)


def is_current(planned: Planned) -> bool:
    """Return whether the file holds the planned bytes (apart from the creation date)."""
    if not planned.path.is_file():
        return False
    stored = _without_date(planned.path.read_bytes())
    return stored == _without_date(planned.rendered)


def _forms(texts: Texts) -> tuple[str, ...]:
    if texts is None:
        return ()
    if isinstance(texts, str):
        return (texts,)
    return tuple(texts)


def _fields(text: str) -> set[str]:
    return set(FIELD.findall(text))


def message_problem(message: Message) -> str | None:
    """Return what is wrong with a message that must be translated, or None."""
    english = _forms(message.id)
    translated = _forms(message.string)
    if message.fuzzy or not translated or not all(translated):
        return f"not translated or fuzzy: {english[0]!r}"
    expected = set().union(*map(_fields, english))
    for text in translated:
        if _fields(text) != expected:
            return f"the fields of {text!r} differ from {english[0]!r}"
    return None


def translation_problems(catalog: Catalog) -> Iterator[str]:
    """Yield what is wrong with a catalog that must translate every text."""
    for message in catalog:
        problem = message_problem(message) if message.id else None
        if problem is not None:
            yield f"{catalog.locale}: {problem}"


def update() -> int:
    """Write every file whose bytes change; return 0."""
    planned, _catalogs = plan(extract())
    for each in planned:
        if not is_current(each):
            each.path.parent.mkdir(parents=True, exist_ok=True)
            each.path.write_bytes(each.rendered)
            sys.stdout.write(f"updated {_relative(each.path)}\n")
    return 0


def check() -> int:
    """Report each file `update` would change and each translation problem; return 1 if any."""
    planned, catalogs = plan(extract())
    stale = (each.path for each in planned if not is_current(each))
    problems = [f"{NOT_CURRENT}: {_relative(path)}" for path in stale]
    for catalog in catalogs:
        if str(catalog.locale) in TRANSLATED_LANGUAGES:
            problems.extend(translation_problems(catalog))
    for problem in problems:
        sys.stdout.write(f"{problem}\n")
    return 1 if problems else 0


def main() -> int:
    """Run the command given on the command line."""
    parser = argparse.ArgumentParser(description="Refresh or check the desktop app's translation catalogs.")
    parser.add_argument("command", choices=("update", "check"))
    args = parser.parse_args()
    return update() if args.command == "update" else check()


if __name__ == "__main__":
    sys.exit(main())
