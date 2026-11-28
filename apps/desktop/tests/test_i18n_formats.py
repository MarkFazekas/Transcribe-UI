"""Numbers, dates, times and durations per language (plan m6 M6.14)."""

from datetime import UTC, datetime

import pytest

from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.constants import HUNGARIAN_GROUP_SEPARATOR, Language
from transcribe_ui.i18n.formats import format_date, format_datetime, format_duration, format_number, format_time

MOMENT = datetime.fromisoformat("2026-11-27T14:05:09+00:00").astimezone(UTC)
MILLIONS = 1_234_567
COST = 0.371
# Durations in seconds: seconds only, minutes and seconds, hours and minutes, below zero.
DURATIONS = (3.2, 303, 3900, -4)
GROUPED_HU = HUNGARIAN_GROUP_SEPARATOR.join(("1", "234", "567"))


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", ("1,234,567", "0.37", "2026-11-27", "2026-11-27 14:05", "14:05:09")),
        ("hu", (GROUPED_HU, "0,37", "2026. 11. 27.", "2026. 11. 27. 14:05", "14:05:09")),
    ],
)
def test_numbers_dates_and_times(language: Language, expected: tuple[str, ...]) -> None:
    """English: comma thousands, decimal point, ISO dates; Hungarian: spaced thousands, decimal comma, `2026. 11. 27.`."""
    activate(language)
    shown = (
        format_number(MILLIONS),
        format_number(COST, 2),
        format_date(MOMENT.date()),
        format_datetime(MOMENT),
        format_time(MOMENT, seconds=True),
    )
    assert shown == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", ("3 s", "5 min 3 s", "1 h 5 min", "0 s")), ("hu", ("3 mp", "5 p 3 mp", "1 ó 5 p", "0 mp"))],
)
def test_durations(language: Language, expected: tuple[str, ...]) -> None:
    """Durations keep their two largest units, rounded to the second (never negative)."""
    activate(language)
    assert tuple(map(format_duration, DURATIONS)) == expected
