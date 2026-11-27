"""Pod name convention (architecture §3.7): build, parse, refuse."""

from datetime import datetime, timedelta

import pytest

from transcribe_contracts.constants import POD_NAME_MAX_LENGTH
from transcribe_contracts.pods import is_app_pod_name, parse_pod_name, pod_name

RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
DEADLINE = datetime.fromisoformat("2026-11-27T14:05:00+00:00")
NAME = f"transcribe-ui-{RUN_ID}-until-20261127T1405Z"


def test_name_round_trips() -> None:
    """A whole-minute UTC deadline gives the documented name and parses back unchanged."""
    name = pod_name(RUN_ID, DEADLINE)
    assert name == NAME
    assert len(name) <= POD_NAME_MAX_LENGTH
    parsed = parse_pod_name(name)
    assert parsed is not None
    assert (parsed.run_id, parsed.deadline) == (RUN_ID, DEADLINE)
    assert is_app_pod_name(name)


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        (DEADLINE + timedelta(microseconds=1), "20261127T1406Z"),
        (datetime.fromisoformat("2026-11-27T14:05:59+00:00"), "20261127T1406Z"),
        (datetime.fromisoformat("2026-11-27T15:05:00+01:00"), "20261127T1405Z"),
        (datetime.fromisoformat("2026-12-31T23:59:30+00:00"), "20270101T0000Z"),
    ],
)
def test_deadline_is_utc_and_rounded_up(deadline: datetime, expected: str) -> None:
    """The name never promises less time than asked; other zones are converted to UTC."""
    assert pod_name(RUN_ID, deadline).endswith(f"-until-{expected}")


@pytest.mark.parametrize(
    ("run_id", "deadline", "message"),
    [
        (RUN_ID, DEADLINE.replace(tzinfo=None), "timezone-aware"),
        (RUN_ID.lower(), DEADLINE, "ULID"),
        ("../../etc", DEADLINE, "ULID"),
    ],
)
def test_builder_refuses_bad_input(run_id: str, deadline: datetime, message: str) -> None:
    """A naive deadline or a run id that is not a canonical ULID is refused."""
    with pytest.raises(ValueError, match=message):
        pod_name(run_id, deadline)


@pytest.mark.parametrize(
    "name",
    [
        f"transcribe-ui-{RUN_ID}",
        f"transcribe-ui-{RUN_ID}-until-20261327T1405Z",
        f"transcribe-ui-{RUN_ID}-until-20261127T2460Z",
        f"transcribe-ui-{RUN_ID}-until-20261127T1405",
        f"transcribe-ui-{RUN_ID}-until-20261127T1405Z-copy",
        f"transcribe-ui-{RUN_ID.lower()}-until-20261127T1405Z",
        f"Transcribe-ui-{RUN_ID}-until-20261127T1405Z",
        "transcribe-ui-manual-test",
        "my-training-pod",
    ],
)
def test_parser_rejects_malformed_names(name: str) -> None:
    """Anything but the exact convention parses to None (the reaper never acts on it)."""
    assert parse_pod_name(name) is None


def test_prefix_check_is_independent_of_parsing() -> None:
    """A malformed name with the prefix is still the app's; a foreign one is not."""
    assert is_app_pod_name("transcribe-ui-manual-test")
    assert not is_app_pod_name("my-transcribe-ui-pod")
