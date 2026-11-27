"""Custom placement plans: export, round trip and every import rejection (architecture §3.5; plan m5 §3 M5.7)."""

import json
from typing import Final

import pytest

from support.placement import (
    MIRROR,
    POSIX_OUTPUT,
    POSIX_ROOT,
    WINDOWS_OUTPUT,
    WINDOWS_ROOT,
    FakeProbe,
    Planning,
    below,
    error_codes,
    has_error,
    item_id,
    jobs_of,
    outcomes,
    plan_of,
    targets,
    windows_path,
)
from transcribe_ui.discovery.rules import PathRules, posix_rules, windows_rules
from transcribe_ui.placement.classes import CustomPlacementOptions, PlacementJob, PlacementPlan
from transcribe_ui.placement.custom import export_custom_plan, import_custom_plan

# Source files of the POSIX cases (below the root): two folders, a same-stem pair, a root file.
POSIX_SOURCES = "A/movie.mkv,A/movie.mp4,B/movie.mkv,top.mkv"
# Field names of a plan entry.
FOLDER: Final = "output_folder"
FILE_NAME: Final = "output_file_name"
# Error codes of the custom import.
INVALID_NAME: Final = "custom_plan_invalid_name"
SCHEMA: Final = "custom_plan_schema"
OUTSIDE: Final = "custom_plan_outside_root"
DUPLICATE_TARGET: Final = "duplicate_target"
# Message part of a reserved Windows name.
RESERVED: Final = "reserves for a device"
# An item number no job has.
UNKNOWN_ITEM = 99
# A file name stem that makes `<stem>.srt` longer than 255 bytes.
LONG_STEM_LENGTH = 252
# A JSON object of a custom plan document.
type JsonObject = dict[str, object]


def _jobs() -> list[PlacementJob]:
    return jobs_of(below(POSIX_ROOT, POSIX_SOURCES))


def _exported(jobs: list[PlacementJob] | None = None) -> JsonObject:
    plan = export_custom_plan(jobs or _jobs(), POSIX_ROOT, posix_rules())
    document: JsonObject = plan.model_dump(mode="json")
    return document


def _entries(document: JsonObject) -> list[JsonObject]:
    entries = document["entries"]
    assert isinstance(entries, list)
    return entries


def _import(
    document: JsonObject | str,
    jobs: list[PlacementJob] | None = None,
    probe: FakeProbe | None = None,
    output: str = POSIX_OUTPUT,
    rules: PathRules | None = None,
) -> PlacementPlan:
    text = document if isinstance(document, str) else json.dumps(document)
    options = CustomPlacementOptions(output_folder=output)
    return import_custom_plan(text, jobs or _jobs(), options, rules or posix_rules(), probe or FakeProbe())


def _edited(index: int, **fields: object) -> JsonObject:
    document = _exported()
    _entries(document)[index].update(fields)
    return document


def test_export_has_mirror_folders_and_names() -> None:
    """The export lists every job once with its source, folder and the default names."""
    entries = _entries(_exported())
    assert [(entry[FOLDER], entry[FILE_NAME]) for entry in entries] == [
        ("A", "movie_mkv.hu.srt"),
        ("A", "movie_mp4.hu.srt"),
        ("B", "movie.hu.srt"),
        ("", "top.hu.srt"),
    ]
    assert entries[0]["original_folder"] == f"{POSIX_ROOT}/A"
    assert entries[0]["source_path"] == f"{POSIX_ROOT}/A/movie.mkv"


def test_round_trip_matches_the_mirror_strategy() -> None:
    """An unedited export imports to the mirror strategy's plan."""
    plan = _import(_exported())
    assert not plan.errors
    assert targets(plan) == targets(plan_of(below(POSIX_ROOT, POSIX_SOURCES), Planning(MIRROR)))
    assert set(outcomes(plan)) == {"place"}


def test_edited_plan_is_placed_where_it_says() -> None:
    """Edited folders and names, `..` inside the root included, are used as given."""
    plan = _import(_edited(0, output_folder="films/../films/2024", output_file_name="Film (2024).hu.srt"))
    assert not plan.errors
    assert targets(plan)[0] == f"{POSIX_OUTPUT}/films/2024/Film (2024).hu.srt"


def test_existing_target_follows_the_overwrite_option() -> None:
    """Imported targets that exist are skipped like in every strategy."""
    probe = FakeProbe(targets={f"{POSIX_OUTPUT}/B/movie.hu.srt": "file"})
    assert outcomes(_import(_exported(), probe=probe))[2] == "skip_existing"


def test_unfinished_jobs_are_listed() -> None:
    """Jobs without a result are listed as not finished, as in every strategy."""
    jobs = jobs_of(below(POSIX_ROOT, POSIX_SOURCES), subtitle="pending")
    assert set(outcomes(_import(_exported(jobs), jobs=jobs))) == {"not_finished"}


def _with_duplicate_entry() -> JsonObject:
    document = _exported()
    entries = _entries(document)
    entries.append(dict(entries[0]))
    return document


def _with_missing_entry() -> JsonObject:
    document = _exported()
    _entries(document).pop()
    return document


def _with_unknown_entry() -> JsonObject:
    document = _exported()
    stranger = dict(_entries(document)[0])
    stranger["item_id"] = item_id(UNKNOWN_ITEM)
    _entries(document).append(stranger)
    return document


def _with_extra_field() -> JsonObject:
    document = _exported()
    _entries(document)[0]["note"] = "LLM comment"
    return document


REJECTIONS = (
    pytest.param("not json", SCHEMA, "does not match its format", id="not-json"),
    pytest.param({"version": 2, "entries": []}, SCHEMA, "version", id="wrong-version"),
    pytest.param({"entries": [{"item_id": "x"}]}, SCHEMA, "entries.0", id="incomplete-entry"),
    pytest.param(_with_extra_field(), SCHEMA, "Extra inputs are not permitted", id="extra-field"),
    pytest.param(_with_duplicate_entry(), "custom_plan_duplicate_job", "listed more than once", id="duplicate-job"),
    pytest.param(_with_missing_entry(), "custom_plan_missing_job", "top.mkv (hu): missing from the plan", id="missing"),
    pytest.param(_with_unknown_entry(), "custom_plan_unknown_job", "is not a job of this download", id="unknown"),
    pytest.param(
        _edited(0, source_path=f"{POSIX_ROOT}/A/other.mkv"),
        "custom_plan_source_changed",
        "source_path or original_folder was changed",
        id="source-changed",
    ),
    pytest.param(
        _edited(0, original_folder=f"{POSIX_ROOT}/B"),
        "custom_plan_source_changed",
        "was changed",
        id="original-folder-changed",
    ),
    pytest.param(_edited(0, output_file_name="movie.hu.txt"), "custom_plan_not_srt", "does not end with", id="not-srt"),
    pytest.param(_edited(0, output_file_name=""), INVALID_NAME, "is not a name", id="empty-name"),
    pytest.param(_edited(0, output_file_name="a\x00b.srt"), INVALID_NAME, "not allowed in a name", id="nul"),
    pytest.param(
        _edited(0, output_file_name=f"{'x' * LONG_STEM_LENGTH}.srt"),
        INVALID_NAME,
        "is longer than 255",
        id="name-too-long",
    ),
    pytest.param(_edited(0, output_folder="../elsewhere"), OUTSIDE, "outside the output folder", id="dot-dot"),
    pytest.param(_edited(0, output_folder="/srv"), OUTSIDE, "outside the output folder", id="absolute-elsewhere"),
    pytest.param(
        _edited(1, output_folder="B", output_file_name="movie.hu.srt"),
        DUPLICATE_TARGET,
        "another job has the same target",
        id="duplicate-target",
    ),
)


@pytest.mark.parametrize(("document", "code", "message"), REJECTIONS)
def test_import_rejections(document: JsonObject | str, code: str, message: str) -> None:
    """Every invalid plan is refused with a code and a message saying what is wrong."""
    assert has_error(_import(document), code, message)


def test_schema_message_has_no_input_values() -> None:
    """Schema messages name locations, not the (path-holding) values."""
    plan = _import(_edited(0, source_path=7, original_folder=["/secret"]))
    assert error_codes(plan) == [SCHEMA]
    assert "/secret" not in plan.errors[0].message


def test_symbolic_link_out_of_the_root_is_refused() -> None:
    """A target that leaves the output folder through a link is refused after resolving it."""
    probe = FakeProbe(links={f"{POSIX_OUTPUT}/A": "/elsewhere"})
    assert error_codes(_import(_exported(), probe=probe)) == [OUTSIDE, OUTSIDE]


def test_linked_output_folder_is_resolved_too() -> None:
    """An output folder that is itself a link compares with targets after resolving both."""
    probe = FakeProbe(links={POSIX_OUTPUT: "/real/out"})
    assert not _import(_exported(), probe=probe).errors


def test_relative_output_folder_is_refused() -> None:
    """The chosen output folder must be absolute."""
    assert error_codes(_import(_exported(), output="out")) == ["output_not_absolute"]


def _windows_import(fields: JsonObject) -> PlacementPlan:
    jobs = jobs_of([windows_path(path) for path in below(WINDOWS_ROOT, "x/movie.mkv,y/movie.mkv")])
    document: JsonObject = export_custom_plan(jobs, windows_path(WINDOWS_ROOT), windows_rules()).model_dump(mode="json")
    _entries(document)[0].update(fields)
    return _import(document, jobs=jobs, output=windows_path(WINDOWS_OUTPUT), rules=windows_rules())


@pytest.mark.parametrize(
    ("fields", "code", "message"),
    [
        ({FILE_NAME: "NUL.srt"}, INVALID_NAME, RESERVED),
        ({FILE_NAME: "com1.hu.srt"}, INVALID_NAME, RESERVED),
        ({FILE_NAME: "movie.srt:hidden"}, INVALID_NAME, "alternate data stream"),
        ({FILE_NAME: "what?.srt"}, INVALID_NAME, "not allowed in a name"),
        ({FOLDER: "x."}, INVALID_NAME, "ends with a dot or a space"),
        ({FOLDER: windows_path("AUX/x")}, INVALID_NAME, RESERVED),
        ({FOLDER: windows_path("E:/x")}, OUTSIDE, "outside the output folder"),
        ({FOLDER: "Y", FILE_NAME: "MOVIE.HU.SRT"}, DUPLICATE_TARGET, "same target"),
    ],
    ids=["nul", "com1", "stream", "question-mark", "trailing-dot", "reserved-folder", "other-drive", "case-duplicate"],
)
def test_windows_import_rejections(fields: JsonObject, code: str, message: str) -> None:
    """The Windows name rules apply to imported names, tested on any OS."""
    assert has_error(_windows_import(fields), code, message)


def test_windows_round_trip() -> None:
    """A Windows export imports to Windows targets."""
    plan = _windows_import({})
    assert targets(plan) == [windows_path(path) for path in below(WINDOWS_OUTPUT, "x/movie.hu.srt,y/movie.hu.srt")]
    assert not plan.errors
