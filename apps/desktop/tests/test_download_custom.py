"""`export_custom_plan` and `import_custom_plan` (plan m5 M5.13): the round trip and its refusals."""

import codecs
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from support.download import (
    EXPORT_PLAN,
    IMPORT_PLAN,
    error_document,
    error_of,
    expected_outcomes,
    ok_document,
    outcomes,
    phase_of,
    plan_error_codes,
    recorded,
    request,
    seed_download_batch,
    send,
    write_subtitles,
)
from support.monitor import SUBTITLE
from support.runs import RunsWorld
from transcribe_ui.api.classes import Response
from transcribe_ui.placement.constants import MAX_CUSTOM_PLAN_BYTES

# The exported plan file and the output folder of the import, below the leg's root.
PLAN_FILE = "<root>/plan.json"
CUSTOM = "<root>/custom"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


def _plan_path(world: RunsWorld) -> Path:
    return world.leg.root / "plan.json"


def _export(world: RunsWorld) -> dict[str, Any]:
    """Export the plan into its file; return the file's plan."""
    exported = ok_document(send(world.leg, request(EXPORT_PLAN, path=PLAN_FILE)))
    on_disk: dict[str, Any] = json.loads(_plan_path(world).read_text(encoding="utf-8"))
    assert exported["plan"] == on_disk
    return on_disk


def _write_plan(world: RunsWorld, plan: dict[str, Any]) -> None:
    _plan_path(world).write_text(json.dumps(plan), encoding="utf-8")


def _import(world: RunsWorld, **fields: object) -> Response:
    options = {"output_folder": CUSTOM, "include_empty": True}
    return send(world.leg, request(IMPORT_PLAN, plan_path=PLAN_FILE, placement=options, **fields))


def test_round_trip(world: RunsWorld) -> None:
    """Export, edit a name and a folder, check, then import: the files go where the edited plan says."""
    plan = _export(world)
    names = [(entry["output_folder"], entry["output_file_name"]) for entry in plan["entries"]]
    assert names == [
        ("Movies", "Film.en.srt"),
        ("Audio", "podcast.en.srt"),
        ("Audio", "Episode.en.srt"),
        ("Movies", "Silent.hu.srt"),
    ]
    plan["entries"][0].update(output_folder="Films/2026", output_file_name="The Film.en.srt")
    _write_plan(world, plan)
    checked = ok_document(_import(world, check_only=True))
    assert (checked["download"], checked["plan"]["errors"], recorded(world.leg)) == (None, [], [])
    imported = ok_document(_import(world))
    assert (outcomes(imported), phase_of(world.leg)) == (expected_outcomes(podcast="place"), "downloaded")
    custom = world.leg.root / "custom"
    film = custom / "Films" / "2026" / "The Film.en.srt"
    assert (film.read_bytes(), (custom / "Audio" / "podcast.en.srt").read_bytes()) == (SUBTITLE, b"")


def test_target_outside_the_output_folder_is_refused(world: RunsWorld) -> None:
    """A plan entry leading out of the output folder is refused with the plan errors; nothing is written."""
    plan = _export(world)
    plan["entries"][1]["output_folder"] = "../elsewhere"
    _write_plan(world, plan)
    assert plan_error_codes(_import(world)) == ["custom_plan_outside_root"]
    assert (recorded(world.leg), (world.leg.root / "custom").exists()) == ([], False)


def test_checked_plan_lists_its_errors(world: RunsWorld) -> None:
    """Only checking a plan with a missing job returns the errors instead of refusing."""
    plan = _export(world)
    plan["entries"].pop()
    _write_plan(world, plan)
    checked = ok_document(_import(world, check_only=True))
    assert [error["code"] for error in checked["plan"]["errors"]] == ["custom_plan_missing_job"]


def test_unreadable_plan_file(world: RunsWorld) -> None:
    """A plan file that does not exist is `custom_plan_unreadable`."""
    assert error_of(_import(world)) == ("custom_plan_unreadable", {"reason": "FileNotFoundError"})


def test_relative_plan_path_is_invalid(world: RunsWorld) -> None:
    """The plan file must be named by an absolute path (the frontends' working folders differ)."""
    options = {"output_folder": CUSTOM}
    refused = send(world.leg, request(IMPORT_PLAN, plan_path="plan.json", placement=options))
    assert error_document(refused)["code"] == "invalid_request"


def test_byte_order_mark_is_accepted(world: RunsWorld) -> None:
    """A plan saved as UTF-8 with a byte order mark (Windows editors) is read like one without."""
    plan = _export(world)
    _plan_path(world).write_bytes(codecs.BOM_UTF8 + json.dumps(plan).encode())
    checked = ok_document(_import(world, check_only=True))
    assert checked["plan"]["errors"] == []


def test_plan_file_over_the_limit(world: RunsWorld) -> None:
    """A plan file over 32 MiB is not read."""
    with _plan_path(world).open("wb") as stream:
        stream.truncate(MAX_CUSTOM_PLAN_BYTES + 1)
    assert error_of(_import(world, check_only=True)) == ("custom_plan_unreadable", {"reason": "too_large"})


def test_plan_path_must_be_a_regular_file(world: RunsWorld) -> None:
    """A FIFO (or a folder) at the plan path is refused at once instead of blocking under the lock."""
    if sys.platform == "win32":
        pytest.skip("FIFOs are POSIX only")
    os.mkfifo(_plan_path(world))
    assert error_of(_import(world)) == ("custom_plan_unreadable", {"reason": "not_a_file"})


def test_export_into_a_missing_folder(world: RunsWorld) -> None:
    """Exporting into a folder that does not exist is `custom_plan_unwritable`."""
    refused = send(world.leg, request(EXPORT_PLAN, path="<root>/missing/plan.json"))
    assert error_of(refused) == ("custom_plan_unwritable", {"reason": "FileNotFoundError"})


def test_target_escaping_through_a_linked_folder(world: RunsWorld) -> None:
    """A plan folder that is a link leading out of the output folder is refused."""
    root = world.leg.root
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (root / "custom").mkdir()
    try:
        (root / "custom" / "linked").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot create symbolic links")
    plan = _export(world)
    plan["entries"][0]["output_folder"] = "linked"
    _write_plan(world, plan)
    assert plan_error_codes(_import(world)) == ["custom_plan_outside_root"]
    assert not list(elsewhere.iterdir())
