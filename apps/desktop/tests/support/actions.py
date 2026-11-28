"""Batches seeded straight into the database in every phase and state the allowed actions depend on.

Each scenario is one batch of its own (`batch_20261127_1300<nn>`), so one database holds them all
and a request sent to one leaves the others alone. Files do not exist on disk: requests that read
them (a review, an edit) fail after their phase guard, which is all the action tests need.
"""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from support.fakes import FIXED_NOW
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, JobPayload, RetryJob, RetryOrigin
from transcribe_ui.discovery.rules import host_rules
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow
from transcribe_ui.state.store import LocalState

# Scan root of every seeded batch below a test's temp folder (it does not exist): absolute on every OS.
SEEDED_FOLDERS = ("nowhere", "media")
# The scenario whose retry batch still copies its audio, and that retry batch.
COPIED_SOURCE = "copied_source"
COPYING_RETRY = "copying_retry"
# The scenario whose retry batch is past its upload (its audio cleaned up since), and that retry batch.
RETRIED_SOURCE = "retried_source"
PAST_RETRY = "past_retry"
# The language of every seeded job.
LANGUAGE = "en"
# Item phases and job statuses of the scenarios.
PENDING = "pending"
UPLOADED_OK = ("uploaded", "ok")
UPLOADED_FAILED = ("uploaded", "failed")
UPLOADED_PENDING = ("uploaded", PENDING)
REVIEWED = ("reviewed", PENDING)
IN_REVIEW = ("reviewing", PENDING)
COMPOSED = ("composing", PENDING)
# Job statuses a retry takes.
FLAGGED = frozenset(("failed", "no_speech"))


@dataclass(frozen=True)
class Scenario:
    """A batch to seed: its phase, its items' phases and transcribe job statuses, its flags."""

    phase: str
    # Per item: (item phase, status of its transcribe job).
    members: tuple[tuple[str, str], ...]
    audio_deleted: bool = False
    # Source scenario of a retry batch (its `retry_of`), or None.
    retry_of: str | None = None
    hidden: bool = False
    # The scope of an interrupted cloud cleanup (`cleanup_pending`), or None.
    cleanup_pending: str | None = None
    # Positions of the items whose job extracts an embedded subtitle instead of transcribing.
    extracting: tuple[int, ...] = ()


# The seeded batches by name, in seeding order (a retry batch after its source).
SCENARIOS = MappingProxyType(
    {
        "composing": Scenario("composing", (COMPOSED, COMPOSED)),
        "reviewing_open": Scenario("reviewing", (IN_REVIEW, REVIEWED)),
        "reviewing_done": Scenario("reviewing", (REVIEWED, REVIEWED)),
        "reviewing_single": Scenario("reviewing", (REVIEWED,)),
        "uploading": Scenario("uploading", (UPLOADED_PENDING, IN_REVIEW)),
        "uploading_done": Scenario("uploading", (UPLOADED_PENDING, UPLOADED_PENDING)),
        "ready": Scenario("ready", (UPLOADED_OK, UPLOADED_PENDING)),
        "transcribed_flagged": Scenario("transcribed", (UPLOADED_OK, UPLOADED_FAILED)),
        "transcribed_clean": Scenario("transcribed", (UPLOADED_OK, UPLOADED_OK)),
        "transcribed_audio_pending": Scenario(
            "transcribed", (UPLOADED_OK, UPLOADED_OK), audio_deleted=True, cleanup_pending="audio"
        ),
        "downloaded_no_speech": Scenario("downloaded", (UPLOADED_OK, ("uploaded", "no_speech"))),
        "downloaded_audio_deleted": Scenario("downloaded", (UPLOADED_OK, UPLOADED_FAILED), audio_deleted=True),
        "downloaded_hidden": Scenario("downloaded", (UPLOADED_OK, UPLOADED_OK), hidden=True),
        "cleaned": Scenario("cleaned", (UPLOADED_OK, UPLOADED_FAILED), audio_deleted=True),
        "cleaned_pending": Scenario(
            "cleaned",
            (UPLOADED_OK, ("uploaded", "extracted")),
            audio_deleted=True,
            cleanup_pending="everything",
            extracting=(1,),
        ),
        COPIED_SOURCE: Scenario("transcribed", (UPLOADED_OK, UPLOADED_FAILED)),
        COPYING_RETRY: Scenario("uploading", (REVIEWED,), retry_of=COPIED_SOURCE),
        RETRIED_SOURCE: Scenario("downloaded", (UPLOADED_OK, UPLOADED_FAILED), audio_deleted=True),
        PAST_RETRY: Scenario("ready", (UPLOADED_PENDING,), retry_of=RETRIED_SOURCE),
    },
)


def scenario_batch_id(name: str) -> str:
    """Return the batch id of a scenario."""
    index = list(SCENARIOS).index(name)
    return f"batch_20261127_1300{index:02d}"


def scenario_item_id(name: str, position: int) -> str:
    """Return the item id of a scenario's item (a valid ULID)."""
    index = list(SCENARIOS).index(name)
    return f"01JD{index:02d}{position:020d}"


def retry_job(name: str) -> dict[str, str]:
    """Return the scenario's first flagged job, else its first job (which a retry refuses)."""
    members = SCENARIOS[name].members
    flagged = [index for index, (_phase, status) in enumerate(members) if status in FLAGGED]
    position = flagged[0] if flagged else 0
    return {"item_id": scenario_item_id(name, position), "language": LANGUAGE}


def seeded_root(tmp_path: Path) -> Path:
    """Return the scan root of the seeded batches (absolute, in this computer's path flavour; it does not exist)."""
    return tmp_path.joinpath(*SEEDED_FOLDERS)


def seed_scenarios(state: LocalState, root: Path) -> None:
    """Store every scenario's batch, its files below `root`."""
    for name, scenario in SCENARIOS.items():
        _seed_batch(state, name, scenario, root)
        for position, member in enumerate(scenario.members):
            kind = "extract" if position in scenario.extracting else "transcribe"
            _seed_item(state, (name, position), (*member, kind), root)


def _seed_batch(state: LocalState, name: str, scenario: Scenario, root: Path) -> None:
    origin = None
    if scenario.retry_of is not None:
        jobs = (RetryJob.model_validate(retry_job(scenario.retry_of)),)
        origin = RetryOrigin(source_batch_id=scenario_batch_id(scenario.retry_of), jobs=jobs)
    payload = BatchPayload(
        scan_root=str(root),
        path_flavor=host_rules().flavor,
        languages=(LANGUAGE,),
        retry_of=origin,
        cleanup_pending=scenario.cleanup_pending,
    )
    row = BatchRow(
        batch_id=scenario_batch_id(name),
        phase=scenario.phase,
        audio_deleted=scenario.audio_deleted,
        hidden=scenario.hidden,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        payload=payload.model_dump(mode="json"),
    )
    with state.session() as session:
        session.add(row)


def _item_payload(position: int, root: Path) -> ItemPayload:
    file_name = f"file{position}.mp3"
    return ItemPayload(
        position=position,
        path=str(root / file_name),
        relative_path=file_name,
        name=file_name,
        kind="audio",
        size_bytes=1,
        mtime_ns=0,
        subtitle_languages=(),
    )


def _seed_item(state: LocalState, where: tuple[str, int], member: tuple[str, str, str], root: Path) -> None:
    name, position = where
    batch_id = scenario_batch_id(name)
    item_id = scenario_item_id(name, position)
    item_phase, status, kind = member
    stored = _item_payload(position, root).model_dump(mode="json")
    job = JobPayload(mode="normal").model_dump(mode="json")
    with state.session() as session:
        session.add(ItemRow(item_id=item_id, batch_id=batch_id, phase=item_phase, payload=stored))
        session.flush()
        session.add(
            JobRow(item_id=item_id, batch_id=batch_id, language=LANGUAGE, kind=kind, status=status, payload=job),
        )
