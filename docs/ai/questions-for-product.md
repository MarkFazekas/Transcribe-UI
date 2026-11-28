# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q37).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q36. App icon

The released app needs an icon: it is shown on the program file in Windows Explorer, in the
macOS Dock and Applications folder, and in the window's title bar and task bar on every system.
Today the app has none, so Windows and macOS would show a generic placeholder icon.

Proposed default: a simple icon made for the app: a speech bubble with two short text lines
(standing for subtitles), in one strong colour on a transparent background, readable from
16×16 pixels up. It is kept in the repository as a source image, so a designed icon can replace
it later without other changes.
