"""A second `uploading` batch for the launch race tests, seeded through the state store (plan m5 M5.11)."""

from support.batches import PODCAST, SEEDED_EXTENSION, SEEDED_OBJECT_BYTES, media_dir
from support.runs import RunsWorld
from transcribe_contracts.layout import audio_key
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, JobPayload
from transcribe_ui.discovery.rules import host_rules
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.models import AudioObjectRow, BatchRow, ItemRow, JobRow
from transcribe_ui.upload.constants import AUDIO_VERIFIED
from transcribe_ui.upload.payloads import AudioObjectPayload


def _podcast_payload(world: RunsWorld) -> ItemPayload:
    path = media_dir(world.leg.root) / "Audio" / PODCAST
    stat = path.stat()
    return ItemPayload(
        position=0,
        path=str(path),
        relative_path=f"Audio/{PODCAST}",
        name=PODCAST,
        kind="audio",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        subtitle_languages=(),
        probe=FakeMediaTools().default_probe,
    )


def seed_audio_batch(world: RunsWorld, batch_id: str, item_id: str) -> None:
    """Seed an `uploading` batch (not held) with the podcast uploaded and verified, its audio in the bucket."""
    member = _podcast_payload(world)
    scan_root = str(media_dir(world.leg.root))
    batch = BatchPayload(scan_root=scan_root, path_flavor=host_rules().flavor, languages=("en",))
    key = audio_key(batch_id, item_id, SEEDED_EXTENSION, None)
    audio = AudioObjectPayload(
        track_index=None,
        extension=SEEDED_EXTENSION,
        codec="mp3",
        duration_s=FakeMediaTools().default_probe.duration_s or 0,
        size_bytes=SEEDED_OBJECT_BYTES,
    )
    now = world.now()
    with world.ports.state.session() as session:
        session.add(
            BatchRow(
                batch_id=batch_id,
                phase="uploading",
                audio_deleted=False,
                hidden=False,
                created_at=now,
                updated_at=now,
                payload=batch.model_dump(mode="json"),
            ),
        )
        session.flush()
        session.add(
            ItemRow(item_id=item_id, batch_id=batch_id, phase="uploaded", payload=member.model_dump(mode="json"))
        )
        session.flush()
        job = JobPayload(mode="normal").model_dump(mode="json")
        session.add(
            JobRow(item_id=item_id, batch_id=batch_id, language="en", kind="transcribe", status="pending", payload=job)
        )
        stored = audio.model_dump(mode="json")
        session.add(
            AudioObjectRow(object_key=key, batch_id=batch_id, item_id=item_id, status=AUDIO_VERIFIED, payload=stored)
        )
    world.store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")
