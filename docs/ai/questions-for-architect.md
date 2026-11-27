# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A21).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A20. One executable for the GUI and the CLI

The release has one single-file executable per platform (`docs/human/working-agreements.md`
"Git and CI": "The only uploaded artifact is the runnable single-file executable";
`architecture.md` §8), so the same file must open the GUI when double-clicked and serve the
JSON CLI (`run`, `schema`, `--version`; M6.7 adds `credentials`) to scripts and LLMs.

Two costs come with one file:

- Windows console. An executable is either a console or a windowed (GUI-subsystem) program,
  fixed at build time. A windowed program cannot serve the CLI: shells do not wait for it and
  its stdout is not connected (`AttachConsole` prints text but does not fix piping, redirection
  or exit codes). A console program serves the CLI fully, but started from Explorer it gets a
  console window. Detecting "the process owns its console alone" in the app does not work
  under the PyInstaller one-file bootloader, which runs two processes (the bootloader and the
  app) on the same console. PyInstaller 6.x handles it in the bootloader instead:
  `--hide-console hide-early` hides the console window at once when the program owns it (started
  from Explorer) and leaves it alone when started from a terminal. A brief flash remains, and
  where Windows Terminal is the default console host the window may not hide at all.
- Start-up cost of the CLI. Every one-file start unpacks the whole bundle to a temp folder, Qt
  included (`research/desktop-packaging.md` §1-§2: 60-150 MB with Qt, 1-3 s per start, more
  with antivirus scanning), so each `run` pays for the GUI it does not use. A script or an LLM
  making many calls (for example polling `refresh_status`) feels this most.

Options:

- One console executable for both (as above): four release files; CLI calls pay the Qt unpack.
- Two executables per platform, a GUI one (windowed, Qt) and a CLI one (console, without Qt,
  much smaller and faster to start): no console flash and a fast CLI, but eight release files,
  against the single-executable rule above.

macOS and Linux have no console split: the raw binary runs in a terminal either way (macOS opens
Terminal on a double-click, M8); the unpack cost applies there too.

Proposed default: one console executable on every platform, built with `--hide-console
hide-early`. With no arguments (a double-click) or with `gui` it opens the GUI; `run`, `schema`,
`--version` and `credentials` are the CLI. Reasons: it keeps the agreed single-file release;
the CLI is used at human or script pace, where 1-3 s per call is acceptable; and the GUI start
sits behind one port (M6.2), so switching to two executables later changes only the build
(M8) and the entry point, not the app. M8's smoke run verifies the hiding on Windows (from
Explorer, from `cmd`, from PowerShell, with Windows Terminal as the default console host) and
measures a CLI call's start time on each platform; if a CLI call takes more than 5 s on a
GitHub runner, the two-executable option comes back to the architect.
