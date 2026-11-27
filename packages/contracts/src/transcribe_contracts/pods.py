"""Names of the Runpod pods and queued pods the app creates (architecture §3.7).

`transcribe-ui-<run_id>-until-<YYYYMMDDTHHMMZ>`: the run id ties the pod to its run, and the
deadline (UTC) tells the external reaper (§5.2, layer 4) when the pod is overdue even if
nothing else about it is known.
"""

import re
from datetime import UTC, datetime

from transcribe_contracts.classes import PodName
from transcribe_contracts.constants import (
    POD_DEADLINE_FORMAT,
    POD_DEADLINE_RESOLUTION,
    POD_NAME_DEADLINE_SEPARATOR,
    POD_NAME_MAX_LENGTH,
    POD_NAME_PATTERN,
    POD_NAME_PREFIX,
    ULID_PATTERN,
)


def pod_name(run_id: str, deadline: datetime) -> str:
    """Return the name of the pod (or queued pod) of a run.

    The deadline is rounded up to the whole minute, so the name never promises less time than
    asked for.

    Args:
        run_id: The run's ULID.
        deadline: Aware moment after which the pod may be terminated from outside.

    Raises:
        ValueError: If the run id is not a ULID or the deadline is naive.
    """
    if re.fullmatch(ULID_PATTERN, run_id) is None:
        msg = "run id must be a canonical ULID"
        raise ValueError(msg)
    if deadline.tzinfo is None:
        msg = "deadline must be timezone-aware"
        raise ValueError(msg)
    utc = deadline.astimezone(UTC)
    minute = utc.replace(second=0, microsecond=0)
    if minute < utc:
        minute += POD_DEADLINE_RESOLUTION
    name = f"{POD_NAME_PREFIX}{run_id}{POD_NAME_DEADLINE_SEPARATOR}{minute.strftime(POD_DEADLINE_FORMAT)}"
    if len(name) > POD_NAME_MAX_LENGTH:
        msg = f"pod name longer than {POD_NAME_MAX_LENGTH} characters"
        raise ValueError(msg)
    return name


def is_app_pod_name(name: str) -> bool:
    """Return True if the name carries the app's prefix (whether or not it parses)."""
    return name.startswith(POD_NAME_PREFIX)


def parse_pod_name(name: str) -> PodName | None:
    """Return the run id and deadline of an app pod name, or None if it is not one."""
    match = re.fullmatch(POD_NAME_PATTERN, name)
    if match is None:
        return None
    deadline_text = match.group("deadline")
    try:
        deadline = datetime.strptime(deadline_text, POD_DEADLINE_FORMAT).replace(tzinfo=UTC)
    except ValueError:  # month 13, minute 61 and the like
        return None
    return PodName(run_id=match.group("run_id"), deadline=deadline)
