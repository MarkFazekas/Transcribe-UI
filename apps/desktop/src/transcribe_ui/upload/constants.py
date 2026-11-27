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

# Batch phase in which `run_upload` starts a batch's upload (its review must be complete).
REVIEWED_BATCH: Final = "reviewing"
# Batch phase of a batch whose upload has started (and is resumed by `run_upload`).
UPLOADING_BATCH: Final = "uploading"
# Batch phases `run_upload` accepts.
UPLOAD_PHASES: Final = frozenset((REVIEWED_BATCH, UPLOADING_BATCH))
# Item phase of an item whose choices are all resolved: it waits for its upload.
ITEM_REVIEWED: Final = "reviewed"
# Item phase of an item still in review (unresolved, or sent back because its source changed).
ITEM_IN_REVIEW: Final = "reviewing"
# Job status of an `extract` job whose SRT is stored locally.
JOB_EXTRACTED: Final = "extracted"
# Folder under the cache dir with one temp folder per batch for its converted audio (one file at a time).
TEMP_FOLDER: Final = "upload"
# Content type of the audio objects (the worker reads the codec from the manifest, not this).
AUDIO_CONTENT_TYPE: Final = "application/octet-stream"
# Codec of a track that was encoded to FLAC because the Matroska muxer refused its own codec.
FLAC_CODEC_NAME: Final = "flac"
# Pattern of an audio object's extension in the R2 layout (as-is audio files keep theirs, lower-cased).
AUDIO_EXTENSION_RULE: Final = r"[a-z0-9]{1,10}"
# Progress stage of `run_upload`: an item is done (`completed` of `total` items).
UPLOAD_ITEM_STAGE: Final = "upload_item"
# Progress stage of `run_upload`: bytes of the current audio object sent so far (`completed` of `total`).
UPLOAD_BYTES_STAGE: Final = "upload_bytes"

# Error code: R2 already holds objects under the batch's id (another computer's batch, or an earlier database's).
ERROR_BATCH_ID_TAKEN: Final = "batch_id_taken"
# Error code: the upload cannot start while an item of the batch is unresolved.
ERROR_REVIEW_INCOMPLETE: Final = "review_incomplete"
# Error code: an uploaded or copied audio object is missing in R2 or has another size than the local file.
ERROR_UPLOAD_UNVERIFIED: Final = "upload_unverified"
# Error code: a file's audio cannot be uploaded as it is (an empty file, or an extension R2 keys do not allow).
ERROR_AUDIO_UNUSABLE: Final = "audio_unusable"
# Error code: an item's file cannot be read right now (an unreachable network share, say); re-sending resumes.
ERROR_SOURCE_UNREADABLE: Final = "source_unreadable"
# Error code: a retry item has no copy source for an audio track its jobs use.
ERROR_COPY_SOURCE_MISSING: Final = "copy_source_missing"
