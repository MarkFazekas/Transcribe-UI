# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A25).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A23. Shape of the macOS GUI file

A20 and the working agreements ask for one-file executables, eight files and nothing else. On
macOS a bare one-file GUI binary works badly for an end user: a browser download loses the
executable bit (`chmod +x` in Terminal), Gatekeeper blocks it until `xattr -d
com.apple.quarantine` is run in Terminal, and a double-click opens a Terminal window next to the
app. PyInstaller's one-file `.app` is deprecated and will be refused in PyInstaller 7
(`research/desktop-release.md`).

Options: (1) bare one-file binary, as A20 reads today. (2) The GUI as a normal `Transcribe-UI.app`
(onedir: starts faster, has its name and icon in the Dock) inside one `.dmg` per architecture:
the user opens the image and drags the app to Applications, no Terminal; the first start of the
unsigned app still needs "Open Anyway" in System Settings once. Still eight files, but the two
macOS GUI files are disk images, not executables. The macOS CLI stays a bare binary (it is used
from Terminal anyway).

Proposed default: (2), for the end-user experience (`plans/m8.md` §1, M8.4). Windows and Linux
are unchanged.

### A24. Manual acceptance of the release builds

CI starts and quits both builds on every platform (`plans/m8.md` §1 "Smoke tests") but cannot
double-click, use the real desktop keyring or check that "Restart now" after a language change
brings a new window to the front (`tooling.md`). The checklist is in `plans/m8.md` §1 "Manual
acceptance", about 10 minutes per OS.

Proposed default: after the first release, the architect runs the checklist on Windows and on
Linux (WSLg on the same computer), or Claude does where the session can drive the desktop;
macOS only if a Mac is at hand, otherwise CI's native macOS start stands in and the language
restart stays unverified on macOS (recorded in the release notes' known limits). M8 is marked
built after the Windows and Linux checks.
