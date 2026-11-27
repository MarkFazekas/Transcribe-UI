"""Every backend code has a catalog entry that fills from its details, in English and in Hungarian (plan m6 M6.14).

The codes are collected from the backend itself (the `ERROR_*`, `CONFIRMATION_*`, `*_STAGE` and
check constants of every `constants.py`, and the code literals of the response models), so a
new code without an entry fails here. Each entry is filled from the details its code comes with:
the samples of `support/code_samples.json` (whose keys are checked against the details the
backend builds, where it builds them without a scenario), and every coded text of every golden
file.
"""

import importlib
import inspect
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, get_args

import pytest
from pydantic import JsonValue

import transcribe_ui
from support.golden import GOLDEN_DIR
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.errors import DomainError
from transcribe_ui.batches.payloads import ProblemCode, ResultErrorCode
from transcribe_ui.cli.run_command import read_request
from transcribe_ui.discovery.classes import ScanWarningCode
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.constants import ERROR_VARIANTS, VARIANT_SEPARATOR, Language, TextKind
from transcribe_ui.i18n.texts import TABLES, code_text
from transcribe_ui.media.invoke import tool_failed
from transcribe_ui.media.runner import ProcessResult
from transcribe_ui.media.tools import ToolLocator, ToolOverrides
from transcribe_ui.placement.classes import DownloadFailureCode, PlanErrorCode, RenameReason
from transcribe_ui.placement.errors import CustomPlanUnreadableError
from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.constants import HTTP_FORBIDDEN
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.inventory import volume_duplicated
from transcribe_ui.runs.errors import CleanupPendingError, RunNotFoundError
from transcribe_ui.state.errors import BatchNotFoundError

# Returned instead of a text when a code has no usable entry.
FELL_BACK = "<fell back>"
LANGUAGES: tuple[Language, ...] = ("en", "hu")
ERROR: Final[TextKind] = "error"
BATCH_ID = "batch_20261127_120000"
RUN_ID = "01JDQ0000000000000000000R1"
FFMPEG: Final = "ffmpeg"
# Details of the code objects, by text kind and entry key (`<code>.<value>` for a variant).
SAMPLES_FILE = Path(__file__).parent / "support" / "code_samples.json"
# Details of a plan error or download failure about one job (the file and its language).
JOB_DETAILS: Mapping[str, JsonValue] = MappingProxyType({"file": "film.mkv", "language": "hu"})
# Kinds whose codes name one job.
JOB_KINDS = frozenset(("plan_error", "download_failure"))


@dataclass(frozen=True)
class Coded:
    """A coded text found in a golden file."""

    kind: TextKind
    code: str
    details: Mapping[str, JsonValue]


# Sample details by entry key.
type Samples = dict[str, dict[str, JsonValue]]


def _samples() -> dict[str, Samples]:
    loaded: dict[str, Samples] = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))
    return loaded


def _is_text(member: object) -> bool:
    return isinstance(member, str)


def _constants(prefix: str = "", suffix: str = "") -> set[str]:
    """Return the string values of the constants named `<prefix>...<suffix>` in every `constants.py`."""
    package = Path(transcribe_ui.__file__).parent
    found: set[str] = set()
    for path in sorted(package.glob("*/constants.py")):
        module = importlib.import_module(f"transcribe_ui.{path.parent.name}.constants")
        for name, text in inspect.getmembers(module, predicate=_is_text):
            if name.startswith(prefix) and name.endswith(suffix):
                found.add(text)
    return found


def _literal(alias: Any) -> set[str]:  # noqa: ANN401 - a `type` alias of a Literal
    return set(get_args(alias.__value__))


def _codes() -> dict[TextKind, set[str]]:
    plan_errors = _literal(PlanErrorCode)
    failures = _literal(DownloadFailureCode)
    return {
        ERROR: _constants("ERROR_") - plan_errors - failures,
        "confirmation": _constants("CONFIRMATION_"),
        "plan_error": plan_errors,
        "download_failure": failures,
        "rename_reason": _literal(RenameReason),
        "check": _constants(suffix="_CHECK_PASSED_CODE"),
        "result_error": _literal(ResultErrorCode),
        "scan_warning": _literal(ScanWarningCode),
        "item_problem": _literal(ProblemCode),
        "price_source": {"fallback"},
        "stage": _constants(suffix="_STAGE"),
    }


def _own_keys(kind: TextKind) -> set[str]:
    """Return the entry keys of a kind; a check's table also holds the error entries."""
    keys = set(TABLES[kind]())
    return keys - set(TABLES[ERROR]()) if kind == "check" else keys


def _base(key: str) -> str:
    return key.split(VARIANT_SEPARATOR, maxsplit=1)[0]


@pytest.mark.parametrize("kind", sorted(TABLES))
def test_every_code_has_an_entry(kind: TextKind) -> None:
    """Each code of the backend has an entry of its own, and no entry outlives its code."""
    assert {_base(key) for key in _own_keys(kind)} == _codes()[kind]


def test_variants_belong_to_variant_codes() -> None:
    """An entry `<code>.<value>` exists only for an error code whose wording depends on a detail."""
    variants = {_base(key) for key in TABLES[ERROR]() if VARIANT_SEPARATOR in key}
    assert variants == set(ERROR_VARIANTS)


def _details(kind: TextKind, key: str) -> Mapping[str, JsonValue]:
    sample = _samples().get(kind, {}).get(key)
    if sample is not None:
        return sample
    if kind in JOB_KINDS:
        return JOB_DETAILS
    variant_of = ERROR_VARIANTS.get(_base(key))
    if variant_of is not None and VARIANT_SEPARATOR in key:
        return {variant_of: key.split(VARIANT_SEPARATOR, maxsplit=1)[1]}
    return {}


def _entries() -> Iterator[tuple[TextKind, str]]:
    for kind in sorted(TABLES):
        for key in sorted(_own_keys(kind)):
            yield kind, key


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(("kind", "key"), list(_entries()))
def test_each_entry_fills_from_its_details(language: Language, kind: TextKind, key: str) -> None:
    """Every entry fills from its code's details (variants from theirs), without falling back."""
    activate(language)
    shown = code_text(kind, _base(key), _details(kind, key), FELL_BACK)
    assert shown != FELL_BACK
    assert "{" not in shown


def _locator_error(locator: ToolLocator) -> Mapping[str, JsonValue]:
    try:
        locator.resolve(FFMPEG)
    except DomainError as error:
        return error.details
    pytest.fail("the lookup did not fail")


def _real_error_details() -> dict[str, Mapping[str, JsonValue]]:
    """Return the details the backend builds for the error codes it builds without a scenario."""
    unreadable = read_request(Path(transcribe_ui.__file__).parent / "missing-request.json")
    assert isinstance(unreadable, ErrorResponse)
    failed = tool_failed("ffprobe", "probe", ProcessResult(1, b"", b"bad"), "ffprobe could not read the file.")
    relative = ToolOverrides(ffmpeg=Path(FFMPEG))
    scope = RunpodApiError("no", status=HTTP_FORBIDDEN, scope="api.pods.write")
    return {
        "request_unreadable": unreadable.error.details,
        "batch_not_found": BatchNotFoundError(BATCH_ID).details,
        "media_tool_not_found": _locator_error(ToolLocator(which=lambda _tool: None)),
        "media_tool_not_found.settings": _locator_error(ToolLocator(overrides=relative)),
        "media_tool_failed": failed.details,
        "runpod_key_missing_scope": api_failure(scope).details,
        "cloud_volume_duplicated": volume_duplicated(("v1", "v2")).details,
        "run_not_found": RunNotFoundError(RUN_ID).details,
        "cleanup_pending": CleanupPendingError([BATCH_ID]).details,
        "custom_plan_unreadable": CustomPlanUnreadableError("FileNotFoundError").details,
    }


def test_samples_match_the_backend() -> None:
    """The samples have the keys of the details the backend builds."""
    samples = _samples()[ERROR]
    for key, details in _real_error_details().items():
        assert set(details) == set(samples[key]), key


def _mapping(found: JsonValue) -> Mapping[str, JsonValue]:
    return found if isinstance(found, dict) else {}


def _entries_of(found: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(found, list):
        return []
    return [entry for entry in found if isinstance(entry, dict)]


def _coded(kind: TextKind, found: JsonValue) -> list[Coded]:
    return [Coded(kind, str(entry["code"]), _mapping(entry.get("details"))) for entry in _entries_of(found)]


def _own_codes(document: Mapping[str, JsonValue]) -> list[Coded]:
    """Return the coded texts a response object holds itself: its error, confirmations, plan errors, failures, checks."""
    found = []
    if document.get("status") == ERROR:
        found.extend(_coded(ERROR, [document.get(ERROR)]))
    found.extend(_coded("confirmation", document.get("confirmations")))
    found.extend(_coded("plan_error", _mapping(document.get("plan")).get("errors")))
    found.extend(_coded("download_failure", _mapping(document.get("download")).get("failures")))
    found.extend(_coded("check", document.get("checks")))
    return found


def _code_objects(document: JsonValue) -> Iterator[Coded]:
    """Yield every coded text of a golden file, at any depth."""
    if isinstance(document, list):
        for entry in document:
            yield from _code_objects(entry)
    elif isinstance(document, dict):
        yield from _own_codes(document)
        for nested in document.values():
            yield from _code_objects(nested)


def _golden_codes() -> list[Coded]:
    found: list[Coded] = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        found.extend(_code_objects(json.loads(path.read_text(encoding="utf-8"))))
    return found


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_golden_text_fills_from_its_details(language: Language) -> None:
    """Every coded text of the golden files (real flows) shows its entry, never the fallback."""
    activate(language)
    found = _golden_codes()
    assert {coded.kind for coded in found} >= {ERROR, "confirmation", "check"}
    for coded in found:
        assert code_text(coded.kind, coded.code, coded.details, FELL_BACK) != FELL_BACK, coded
