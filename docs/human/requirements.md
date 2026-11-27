# Requirements (human input)

Source: the architect's initial brief and the later answers of the architect and of product
(2026-11-26), translated to English. This file records what was asked and decided by humans,
not how it is implemented. Implementation lives in `docs/ai/`. Where a proposal was accepted
as proposed ("default"), the accepted proposal is written out and marked *(accepted proposal)*.

Reference project: https://github.com/MarkFazekas/STTCLI (its Runpod instructions were used
several times and worked).

Terms: a **job** is one run of transcriptions that the user starts (internally a batch); the
**cloud machine** is the rented GPU computer (Runpod pod) that does the transcription.

## System overview

Two parts:

1. **Deployed app (worker)**: a Runpod image, pushed to public Docker Hub, that downloads the
   audio batches waiting for transcription, plus their relevant metadata, from an
   S3-compatible API (Cloudflare R2).
2. **UI app (desktop)**: a Python UI/CLI using FFmpeg that recursively discovers video and
   audio files in a chosen folder, lists them and lets the user select which ones to
   transcribe.

## Main window

- Main window layout *(Q33, architect: "A+C")*. The options were shown as mockups:
  - A: a left-side navigation with four pages (New job, Jobs, Cloud, Settings) and an
    always-visible cloud status bar at the bottom.
  - B: top tabs.
  - C: a step-by-step wizard (1 Files → 2 Upload → 3 Transcription → 4 Download) with Back and
    next buttons.
- The architect, with the answer: "During conversion to audio I can start another file
  selection." (Answer: yes.)

## UI app: file selection

- Audio/video files and the transcription language(s) are selectable.
- Listed files: all common video and audio file types (MP4, MKV, MOV, AVI, WebM, MP3, M4A,
  WAV, FLAC, OGG, Opus and similar); the list can be adjusted later in the settings
  *(accepted proposal)*.
- Hidden and system folders are not listed (a setting can include them); folder
  shortcuts/links are not followed, so the same file never appears twice *(accepted proposal)*.
- Languages: a global app-level setting lists every language the transcription supports, each
  with an on/off switch. Hungarian and English are on by default. Enabled languages appear in
  the language choosers and filters ("if someone turns on DE, it appears in the language
  chooser too, obviously").
- Spoken language: the user chooses it. No automatic detection for now: no small model on the
  user's computer and no guess on the cloud machine. Automatic detection is kept as an idea
  for later.
- If a file already has a subtitle, show it. Recognised subtitle files: the same name as the
  video, with or without a language in the name (`movie.srt`, `movie.hu.srt`,
  `movie.hun.srt`, `movie.hungarian.srt`, `movie.en.forced.srt`; also `.vtt`, `.ass`, `.ssa`).
  A file without a language, like `movie.srt`, counts as "subtitle in an unknown language": it
  matches "has any subtitle" but not "has Hungarian" *(accepted proposal)*.
- The "has subtitle" filter in the main view counts only separate subtitle files; subtitles
  inside video files are handled in the review step.
- Search/filter for files that already have a subtitle in some language, so the user can, for
  example, select those and request an English subtitle for them as well.
- The folder structure is shown as a tree and selection works on the tree: opening `D:\` and
  selecting `D:\A\C` selects every audio/video file in `C` and all its subfolders.
- When a filter is active, folder selection only selects matching files. Filters include:
  - subtitle: pick any set of the enabled languages and a rule: "has all of them", "has any of
    them", or "is missing at least one of them" (covers "Hungarian subtitle but no English
    one") *(accepted proposal)*
  - audio file, video file
  - file name starts with / ends with / contains / regex
- A file can be transcribed again into a language it already has a subtitle for, but the app
  warns before the job is created. When a whole folder is selected while the filter "is
  missing <language>" is on, such files are not selected *(accepted proposal)*.

## UI app: review step

After the file selection there is a clarifying step. Both parts have the same language and
folder filters as the main view, so choices can be made per folder (one language for one
folder, another for another folder).

1. Files that carry a subtitle inside the file are listed; per file the user chooses:
   transcribe, or just extract the existing subtitle. Picture-based subtitles (DVD/Blu-ray)
   cannot be extracted as text, so for those only transcription is offered.
2. Files with more than one soundtrack are listed in one summary view with each track's
   language; the user picks the track per file.

## UI app: conversion and upload

- A video file is converted to audio in a temporary folder, losslessly where possible, using
  stream copy (as the reference repo did).
- Each file's original absolute path is stored in the batch metadata.
- Each file gets a unique ULID; it is stored in object storage under that name.

## Batches

- Batch ID is not a ULID; for example `batch_YYYYMMDD_HHmmSS`. It is also the S3 "folder"
  (prefix) name.
- Batch metadata files are uploaded to a `batch_metadata` folder.
- Batch data is stored locally in the OS-appropriate user folder.
- One batch per selection, even with several languages: each file lists its target languages
  and its audio is uploaded once. The worker processes the jobs language by language (all `en`
  jobs, then all `hu` jobs, …), because switching the language reloads a per-language
  component, so en, en, hu is faster than en, hu, en.
- Batch phases:
  1. Local composition: files are being selected.
  2. Converting and uploading: files are converted to audio one by one and uploaded
     immediately.
  3. Upload complete: the batch is ready for transcription.
  4. Transcription complete: subtitles are ready to download.
  5. Subtitles downloaded: they can be deleted from storage.
  6. Manual confirmation of deleting the subtitles and the input audio files.
- Every step must be interruptible and resumable, both during and between steps.
- Closing the app while work is in progress *(Q30, accepted default)*: if a step is running
  (converting and uploading, downloading subtitles, cleaning up the cloud), the app asks "Stop
  and close? The work continues from where it stopped the next time you start it." On yes, it
  stops the step cleanly (this can take a few seconds; a "Quit now" button ends at once, still
  without losing finished work). If a cloud machine is waiting or working, the close question
  also says that the machine keeps working without the app, stops itself when the work is done
  or at its time limit, and that the app shows its results the next time it starts. Closing
  never cancels the cloud machine; cancelling is a separate button on the Cloud page.
- Steps the app starts on its own *(Q31, accepted default)*: the app starts none of these on
  its own: the upload after the review step, the start of transcription after an upload, the
  download after transcription, and continuing an interrupted upload or download when the app
  starts again. Each is one button (Upload at the end of the review step, Start transcription
  on the Cloud page, Download on the job page, Resume on an interrupted job). The app only
  checks the cloud machines at start-up and follows a running one, and it shows clearly which
  jobs are interrupted or have subtitles ready.
- Job history is kept forever, with an option to hide finished and cleaned-up jobs. History
  only contains file names, paths and statuses (no audio) *(accepted proposal)*.

## Cloud cleanup

Cleaning up the cloud is available after the download and at any time when the job is opened,
as a maintenance action: "delete audio only" or "delete everything (audio and subtitles)".
Deleting something that is already gone is not an error.

- Abandoning a job before it is uploaded *(Q26, accepted default)*: a job in the review step
  can still be deleted (nothing is in the cloud yet; it disappears from the history). A job
  whose upload has started can be abandoned with the cloud cleanup "delete everything": the
  uploaded audio and unfinished uploads are deleted, and the job stays in the history as
  cleaned up.

## Subtitle download options

- Next to the original video/audio file.
- Into a chosen folder that mirrors the original tree structure.
- Into one folder without structure.
- Into one folder without structure, keeping selectable folder names joined with underscores.
  The user chooses which folder names are kept; by default the X folders closest to the file
  (`C:\A\B\C\D\e.mkv` keeping 2 folders → `C_D_e.hu.srt`).
- Custom: export a JSON containing each file's name, original folder and the output file and
  folder name; the user fills it in with an LLM and imports it. The import must be validated.

Naming and conflicts:

- Format: SRT only.
- New subtitle name: the video's name plus a two-letter language code, `movie.hu.srt`
  *(accepted proposal)*.
- An existing subtitle file with the same name is listed before the download; skipped by
  default, overwrite selectable. Low effort: this can practically only happen if the file was
  transcribed while the batch was being started.
- Original folder gone *(Q23, accepted default)*: the job remembers where each original file
  was. If by download time the file's folder was moved, renamed or deleted, the download plan
  lists the file with the reason "original folder not found" and skips it; every other subtitle
  is placed. The user can download the skipped ones again with another option (for example into
  a chosen folder).
- Download before the whole job is done *(Q24, accepted default)*: finished subtitles can be
  downloaded at any time, also while the job is waiting or being transcribed. The download plan
  lists the files still without a subtitle as "not finished yet" and skips them. The job counts
  as downloaded once it is fully transcribed and every file has been through a download (placed,
  or skipped as an existing file, a missing folder or an empty result), whether in one download
  or several. The next start of transcription does the missing files. A failed file (no subtitle
  could be made) does not keep the job from counting as downloaded; retry batches handle failed
  jobs.
- Download again *(Q25, accepted default)*: a downloaded job can be downloaded again any number
  of times, with any placement option, as long as its subtitles are still in the cloud (not
  after "delete everything"; subtitles taken from inside the video files are kept on the
  computer and can be placed again even after that). Files already in place are listed as
  existing and skipped unless overwrite is chosen. This is also how the files skipped because
  their original folder is gone are placed later.
- Two subtitles that would get the same name in one folder are shown; by default each gets its
  folder name as prefix: `A_movie.hu.srt`, `B_movie.hu.srt`. No numbering.

## Runpod control

- A UI button starts the Runpod pod via the given API. One button sends every job that has
  finished uploading. The app then shows the state: waiting for a free cloud machine,
  starting, transcribing (which job and file, elapsed time, cost so far), finished, or failed.
  While waiting for a machine, the user can cancel *(accepted proposal)*.
- Run monitoring (architect): the status should be refreshed from R2 every 5 minutes, and the
  remaining time should be estimated from the files' completion. The estimated time should be a
  countdown, and maybe there should be a progress bar too.
- The app asks at every start how long to wait for a free machine; default 1 hour, at any time
  of day.
- Maximum wait for a machine (A19, architect decision): the wait is 5 min to 24 h, with a 1 h
  default. `MAX_RUNTIME_S` is at most 24 h. The worst case is under 50 h, about 13 USD.
- Before starting, the app shows the total audio length and an estimated cost range and asks
  for confirmation. No separate spending limit in the app; the cloud account's own spending
  limit applies *(accepted proposal)*.
- Cloud jobs this computer does not know *(Q28, architect)*: the cloud machine transcribes
  every job it finds in the cloud storage, including jobs uploaded from another computer or by
  an earlier installation whose local history is gone. The start confirmation lists these jobs
  (job id, number of files, audio length) and includes them in the audio length and cost
  estimate. The user can delete such a job from the cloud from that list, with one
  confirmation, before starting *(proposed default, not objected to)*.
  - Architect, with the answer: "List them, and include them in the time and cost. Mainly
    because there can be a setup where an employee has access only to R2 but not to Runpod. So
    an instance on one machine uploads the things, and another person starts it from another
    instance on another machine. Afterwards the one who started the process downloads it. But
    the Runpod supervisor could also download it, keeping the folder and file name structure."
- The S3-compatible credentials and the Runpod API key are stored in the OS credential store.
- R2 credentials for the pod (A18, accepted default): there is one bucket-scoped R2 token,
  shared by the desktop and the pod. `setup_cloud` creates or updates the Runpod secrets from
  the R2 credentials in the desktop keyring, then the template that references them. Rotating
  the token means re-running `setup_cloud`.
- Upload finishing during a run *(Q27, accepted default; architect: "let it be the Start
  button")*: the launch scope is the set of batches that are fully uploaded when the user
  presses Start. The maximum runtime, the cost estimate and the pod name deadline are worked out
  from that scope and fixed in the pod request. A batch whose upload finishes after Start, while
  the run is queued, waiting or running, completes its upload and waits ("uploaded, waits for
  the current run"). It is not given to that run, and it becomes ready for the next Start once
  the current run ends.

## Deployed app (worker)

- Pod: RTX A5000, 1 GPU, On-Demand pricing, 60 GB container disk, 1 GB Global Volume
  persistent disk.
- "Deploy when available" is needed at start, because RTX A5000 is not always available.
- Written in Python, not bash, with the same quality standards as the UI app.
- Automatic operation is expected: the pod started from the UI transcribes every batch that is
  ready for transcription, saves the output to persistent storage as a backup, uploads the
  output per batch, then stops the pod.
- Engine: output quality comes first, so word-timestamp alignment is kept (WhisperX with
  forced alignment rather than plain faster-whisper).
- Subtitle format *(Q22, accepted default)*: lines hold at most 42 characters for every
  language, including Chinese and Japanese (Hungarian and English are the main languages);
  revisit if Chinese or Japanese subtitles are actually used.
- Model weights are baked into the Docker image so the pod starts faster.
- No SSH server in the image: SSH goes through Runpod's own system.
- R2 credentials reach the pod as Runpod secrets.
- Several cloud backup volumes with the app's name *(Q29, accepted default; architect: "This
  is over-engineering an edge case.")*: the app keeps one backup volume on Runpod for the cloud
  machine's result backups. If Runpod holds two or more with the app's name, the app never
  deletes one itself and refuses to start a cloud machine until only one is left. The cloud
  setup and the start show an error listing the volumes' ids; the user deletes the unwanted
  volume in the Runpod console (Storage page) and runs the setup again.
- Docker Hub repository: `markfazekasgh/transcribe-ui-worker` (public). CI pushes with a
  token kept in a GitHub environment that only the `main` branch can use.
- In the first test period the pod is not stopped automatically; the architect will SSH in.
  The time limit in test mode is 2 hours.
- Base images suggested by the Runpod UI (evaluated in `docs/ai/architecture.md`):
  `runpod/pytorch:2.1.0-cu1281-torch280-ubuntu2404`, `…-cu1290-…`, `…-cu1300-…`,
  `…-cu1320-…`.

### Failure handling

- A file that cannot be transcribed, or has empty output, must not block the process. The app
  marks these files in the job view with the reason ("no speech found" or "failed: …") and a
  Retry button; it does not put empty subtitle files next to the videos unless the user asks
  for it *(accepted proposal)*.
- Retry starts a new job with only the selected marked files and the same language; the audio
  already in the cloud is reused, and the retry uses a slower, more careful transcription
  setting *(accepted proposal)*.
- The Runpod pod must never be left running, not even after a major failure. A scheduled
  external job (GitHub Actions, pods-only key) terminates forgotten pods.
- Architect decision (2026-11-27): the reaper's public GitHub Actions log may keep showing pod
  ids, run ids and deadlines (taken from pod names) and action times. Reason: low risk, since
  nothing can be done with a pod id without the key, and the key is masked.
- A failed transcription or an interrupted batch must not harm the successful transcriptions.
- Batch handling should be smarter than "download all audio files, then upload everything".

## Architecture and code quality

- Project language: English (code, docs, commits, PR texts, product questions).
- Language of the app's texts *(Q32, architect)*: "What is Django's language file? .po? Let
  there be a Hungarian and an English .po file, and the OS language. If the OS language is
  Hungarian, the app is Hungarian; the default is English." And: "It should be possible to
  change the language in Settings."
- Very high code quality standard.
  Quality and the end-user experience are the first and main principle
  (`working-agreements.md`).
- Business logic is separated from the UI. The backend logic must be usable from the CLI and
  the UI. Frontend and backend live in one app; the boundary is internal and validated with
  Pydantic.
- CLI input is a Pydantic-validated JSON file (the surface is too complex for flags). The UI
  talks to the backend with the same Pydantic models.
- The system must be ready for a FastMCP input mode later (not built now).
- The backend must return the same answer for the same Pydantic JSON input, regardless of
  whether it came from the UI, CLI or (later) FastMCP. This must be testable.
- Tests for both the backend and the deployed app: few unit tests, mostly regression and flow
  tests.
- Managed with uv, as one workspace (`packages/contracts`, `apps/desktop`, `apps/worker`);
  Python 3.13 everywhere.
- Desktop stack *(accepted proposal)*: PySide6, PyInstaller one-file, static LGPL ffmpeg
  bundled, unsigned builds for four platforms.
- Two executables per platform *(A20, architect decision)*: a GUI executable (windowed, GUI
  subsystem, contains Qt, opens the GUI) and a CLI executable (console, without Qt; serves `run`,
  `schema`, `--version` and `credentials`). The release has eight files, two per platform.
- Local state: SQLite with SQLAlchemy and Alembic.
- Code layout, Django-like: constants per folder / app / sub-app in a `constants.py`, each with
  a roughly one-line explanation of its purpose, not with an underscore at the top of other
  files; `models.py` for the SQL models, `classes.py` for the Pydantic models, a separate
  `migrations` folder for migrations.
- Lint setup taken from
  https://github.com/MarkFazekas/ApplicantFilterSystemO/tree/main/manager_backend : flake8 +
  ruff + mypy. Ruff does not cover every flake8 plugin, so the full `.flake8` and ruff configs
  are carried over, adapted to this project.
- Conventional commits.
- On push to the main branch, build executables for Windows, macOS and Ubuntu, and create a
  GitHub Release with them.
- A Claude hook runs a code review in a sub-agent before the session returns with code or any
  other change.

## Documentation

- Documentation lives in `docs/`.
- Human input (what the architect/product says) goes to `docs/human/`; what Claude produces
  from inputs and discoveries goes to `docs/ai/`.
- Keep information that is useful later; do not write a history book.
- Answered questions are removed from the question files; the answers and what follows from
  them go to their place in the docs.
- `CLAUDE.md`, `README.md` and `.gitignore` are part of the repo. Docs handling rules are in
  `CLAUDE.md`.
