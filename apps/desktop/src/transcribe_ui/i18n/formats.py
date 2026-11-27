"""Numbers, dates, times and durations as the active language writes them (architecture §3.1).

English: `1,234.5`, `2026-11-27`, `14:05`; Hungarian: `1 234,5` (a no-break space between the
thousands), `2026. 11. 27.`, `14:05`. Both use the 24-hour clock. Durations are texts of the
catalog (`1 h 5 min`, `1 ó 5 p`). The caller converts a moment to the zone it shows.
"""

from datetime import date, datetime, time

from transcribe_ui.i18n.catalog import active_language, tr
from transcribe_ui.i18n.constants import HUNGARIAN, HUNGARIAN_GROUP_SEPARATOR, SECONDS_PER_HOUR, SECONDS_PER_MINUTE


def format_number(number: float, decimals: int = 0) -> str:
    """Return a number with `decimals` decimals and grouped thousands."""
    english = f"{number:,.{decimals}f}"
    if active_language() != HUNGARIAN:
        return english
    grouped = english.replace(",", HUNGARIAN_GROUP_SEPARATOR)
    return grouped.replace(".", ",")


def format_date(day: date) -> str:
    """Return a calendar date (`2026-11-27`, Hungarian `2026. 11. 27.`)."""
    if active_language() == HUNGARIAN:
        return f"{day.year}. {day.month:02d}. {day.day:02d}."
    return day.isoformat()


def format_time(moment: time | datetime, *, seconds: bool = False) -> str:
    """Return a time of day on the 24-hour clock (`14:05`, with `seconds` `14:05:09`)."""
    shown = f"{moment.hour:02d}:{moment.minute:02d}"
    return f"{shown}:{moment.second:02d}" if seconds else shown


def format_datetime(moment: datetime, *, seconds: bool = False) -> str:
    """Return a date and a time of day (`2026-11-27 14:05`)."""
    day = format_date(moment.date())
    return f"{day} {format_time(moment, seconds=seconds)}"


def format_duration(total_s: float) -> str:
    """Return a duration rounded to the second, with its two largest units (`1 h 5 min`, `5 min 3 s`, `3 s`)."""
    rounded = max(round(total_s), 0)
    hours, rest = divmod(rounded, SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest, SECONDS_PER_MINUTE)
    if hours:
        return tr("{hours} h {minutes} min").format(hours=format_number(hours), minutes=minutes)
    if minutes:
        return tr("{minutes} min {seconds} s").format(minutes=minutes, seconds=seconds)
    return tr("{seconds} s").format(seconds=seconds)
