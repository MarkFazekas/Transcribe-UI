# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q24).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q22. Line length of Chinese and Japanese subtitles

Subtitle lines hold at most 42 characters. Chinese and Japanese characters are about twice as
wide on screen as Latin letters, so a 42-character line in these languages is roughly twice as
wide as an English one and may not fit the screen.

Proposed default: keep 42 characters for every language for now (Hungarian and English are the
main languages); revisit if Chinese or Japanese subtitles are actually used.

### Q23. Subtitles "next to the original file" when that folder is gone

The job remembers where each original file was. If, by the time the subtitles are downloaded,
the file's folder was moved, renamed or deleted, the subtitle cannot be put next to it.

Proposed default: the download plan lists these files with the reason "original folder not
found" and skips them; every other subtitle is placed. The user can download the skipped ones
again with another option (for example into a chosen folder).
