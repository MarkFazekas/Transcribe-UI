# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A25).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A24. Manual acceptance of the release builds

CI starts and quits both builds on every platform (`plans/m8.md` §1 "Smoke tests") but cannot
double-click, use the real desktop keyring or check that "Restart now" after a language change
brings a new window to the front (`tooling.md`). The checklist is in `plans/m8.md` §1 "Manual
acceptance", about 10 minutes per OS; on Windows it includes the antivirus check (downloading
through a browser with Defender's real-time protection on), which CI cannot do.

Proposed default: after the first release, the architect runs the checklist on Windows and on
Linux (WSLg on the same computer), or Claude does where the session can drive the desktop;
macOS only if a Mac is at hand, otherwise CI's native macOS start stands in and the language
restart stays unverified on macOS (recorded in the release notes' known limits). M8 is marked
built after the Windows and Linux checks.
