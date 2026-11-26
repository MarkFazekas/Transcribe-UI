# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q23).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q22. Line length of Chinese and Japanese subtitles

Subtitle lines hold at most 42 characters. Chinese and Japanese characters are about twice as
wide on screen as Latin letters, so a 42-character line in these languages is roughly twice as
wide as an English one and may not fit the screen.

Proposed default: keep 42 characters for every language for now (Hungarian and English are the
main languages); revisit if Chinese or Japanese subtitles are actually used.
