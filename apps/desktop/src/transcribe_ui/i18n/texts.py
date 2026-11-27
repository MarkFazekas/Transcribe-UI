"""The text of a backend code in the active language, for every kind of code (§3.1 "Language of the texts").

`code_text` shows the catalog entry of the code formatted with its details, and the backend's
English text (or the code itself) only for a code without an entry. A credential check that
failed has an error code, so checks fall back to the error entries.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType

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
from transcribe_ui.i18n.constants import ERROR_VARIANTS, TextKind
from transcribe_ui.i18n.error_texts import error_texts


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
# The kinds whose codes are error codes (with their variants).
ERROR_KINDS = frozenset(("error", "check"))


def code_text(
    kind: TextKind,
    code: str,
    details: Details | None = None,
    fallback: str | None = None,
) -> str:
    """Return the text of a code of `kind`, formatted with its details; `fallback` (else the code) without an entry."""
    variants = ERROR_VARIANTS if kind in ERROR_KINDS else {}
    shown = {} if details is None else details
    english = code if fallback is None else fallback
    return render_code(TABLES[kind](), code, shown, english, variants)
