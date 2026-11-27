"""Constants of the manifest step (architecture §3.3)."""

from typing import Final

# Status of an `audio_objects` row whose upload was verified (HEAD size matches).
AUDIO_VERIFIED: Final = "verified"
# Item phase of an item whose audio is verified and whose extractions are stored.
ITEM_UPLOADED: Final = "uploaded"
# Content type of a manifest object.
MANIFEST_CONTENT_TYPE: Final = "application/json"

# Error code: the batch still has items in review or audio objects not verified; no manifest yet.
ERROR_UPLOAD_INCOMPLETE: Final = "upload_incomplete"
# Error code: the batch has no `transcribe` job, so it gets no manifest (the extract-only path).
ERROR_NOTHING_TO_TRANSCRIBE: Final = "nothing_to_transcribe"
