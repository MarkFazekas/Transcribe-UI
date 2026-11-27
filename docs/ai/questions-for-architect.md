# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A21).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A20. One executable for the GUI and the CLI

The release has one single-file executable per platform (requirements, §8), so the same file
must open the GUI when double-clicked and serve the JSON CLI (`run`, `schema`, `--version`) to
scripts and LLMs. On Windows an executable is either a console or a windowed (GUI-subsystem)
program, fixed at build time:

- Console program: the CLI works fully (stdout, stderr, exit code; shells wait for it).
  Started from Explorer, Windows opens a console window for it; the GUI start detaches from it
  (`FreeConsole` when the process is the console's only one), so the window flashes briefly.
- Windowed program: no console flash, but shells do not wait for it and stdout is not connected;
  `AttachConsole` to the parent console prints text but does not fix piping, redirection or
  exit codes reliably, which the CLI's contract needs.
- Two executables (GUI and CLI): clean on Windows, but the release would carry eight files
  instead of four, against "only the runnable single-file executable".

macOS and Linux have no such split: the raw binary runs in a terminal either way (macOS opens
Terminal on a double-click, M8).

Proposed default: one console-subsystem executable on every platform. With no arguments (a
double-click) or with `gui` it opens the GUI, detaching from a console it owns alone on Windows
(the brief flash is accepted); `run`, `schema`, `credentials` and `--version` are the CLI.
M6.2 builds the entry point and the detach behind a port; M8 builds the executables.
