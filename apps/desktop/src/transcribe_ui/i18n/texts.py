"""The text of a backend code in the active language, for every kind of code (§3.1 "Language of the texts").

`code_text` shows the catalog entry of the code formatted with its details, and the backend's
English text (or the code itself) only for a code without an entry. A credential check that
failed has an error code, so checks fall back to the error entries.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.i18n.code_texts import (
    check_texts,
    confirmation_texts,
    download_failure_texts,
    item_problem_texts,
    plan_error_texts,
    price_source_texts,
    rename_reason_texts,
    result_error_texts,
    scan_warning_texts,
    stage_texts,
)
from transcribe_ui.i18n.codes import CodeTable, Details, render_code
from transcribe_ui.i18n.constants import ERROR_VARIANTS, PLAN_ERROR_VARIANTS, TextKind
from transcribe_ui.i18n.error_texts import error_texts
from transcribe_ui.i18n.formats import format_number


def _checks() -> CodeTable:
    return {**error_texts(), **check_texts()}


# The entries of each kind of code, built in the active language when asked for.
TABLES: Mapping[TextKind, Callable[[], CodeTable]] = MappingProxyType(
    {
        "error": error_texts,
        "confirmation": confirmation_texts,
        "plan_error": plan_error_texts,
        "download_failure": download_failure_texts,
        "rename_reason": rename_reason_texts,
        "check": _checks,
        "result_error": result_error_texts,
        "scan_warning": scan_warning_texts,
        "item_problem": item_problem_texts,
        "price_source": price_source_texts,
        "stage": stage_texts,
    },
)
# The variant keys of the kinds that have variants (a check's codes are error codes).
VARIANTS: Mapping[TextKind, Mapping[str, str]] = MappingProxyType(
    {"error": ERROR_VARIANTS, "check": ERROR_VARIANTS, "plan_error": PLAN_ERROR_VARIANTS},
)


def code_text(
    kind: TextKind,
    code: str,
    details: Details | None = None,
    fallback: str | None = None,
) -> str:
    """Return the text of a code of `kind`, formatted with its details; `fallback` (else the code) without an entry."""
    variants = VARIANTS.get(kind, {})
    shown = {} if details is None else details
    english = code if fallback is None else fallback
    return render_code(TABLES[kind](), code, shown, english, variants)


def progress_text(event: ProgressEvent) -> str:
    """Return a progress event as "stage completed/total" (no total: "stage completed"), the stage translated.

    Shared by the GUI's activity panel and the CLI's `--progress text` (which adds the path).
    """
    stage = code_text("stage", event.stage)
    done = format_number(event.completed)
    if event.total is None:
        return f"{stage} {done}"
    return f"{stage} {done}/{format_number(event.total)}"
