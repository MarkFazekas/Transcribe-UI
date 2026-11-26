# Requirements (human input)

Source: the architect's initial brief (2026-11-26), translated to English. This file records
what was asked, not how it is implemented. Implementation decisions live in `docs/ai/`.

Reference project: https://github.com/MarkFazekas/STTCLI (its Runpod instructions were used
several times and worked).

## System overview

Two parts:

1. **Deployed app (worker)**: a Runpod image, pushed to public Docker Hub, that downloads the
   audio batches waiting for transcription, plus their relevant metadata, from an
   S3-compatible API (Cloudflare R2).
2. **UI app (desktop)**: a Python UI/CLI using FFmpeg that recursively discovers video and
   audio files in a chosen folder, lists them and lets the user select which ones to
   transcribe.

## UI app: file selection

- Audio/video files and the transcription language(s) are selectable.
- Hungarian and English are offered by default; any other supported language can be chosen.
- If a file already has a subtitle, show it.
- Search/filter for files that already have a subtitle in some language, so the user can, for
  example, select those and request an English subtitle for them as well.
- The folder structure is shown as a tree and selection works on the tree: opening `D:\` and
  selecting `D:\A\C` selects every audio/video file in `C` and all its subfolders.
- When a filter is active, folder selection only selects matching files. Filters include:
  - has subtitle: any / English / Hungarian / both
  - audio file, video file
  - file name starts with / ends with / contains / regex

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
- Whisper is understood to require per-language batches, so batches are built per language.
  The UI allows choosing several languages at once; then one batch is built per language.
- Batch phases:
  1. Local composition: files are being selected.
  2. Converting and uploading: files are converted to audio one by one and uploaded
     immediately.
  3. Upload complete: the batch is ready for transcription.
  4. Transcription complete: subtitles are ready to download.
  5. Subtitles downloaded: they can be deleted from storage.
  6. Manual confirmation of deleting the subtitles and the input audio files.
- Every step must be interruptible and resumable, both during and between steps.

## Subtitle download options

- Next to the original video/audio file.
- Into a chosen folder that mirrors the original tree structure.
- Into one folder without structure.
- Into one folder without structure, keeping selectable folder names joined with underscores.
- Custom: export a JSON containing each file's name, original folder and the output file and
  folder name; the user fills it in with an LLM and imports it. The import must be validated.

## Runpod control

- A UI button starts the Runpod pod via the given API.
- The S3-compatible credentials and the Runpod API key are stored in the OS credential store.

## Deployed app (worker)

- Pod: RTX A5000, 1 GPU, On-Demand pricing, 60 GB container disk, 1 GB Global Volume
  persistent disk.
- "Deploy when available" is needed at start, because RTX A5000 is not always available.
- Written in Python, not bash, with the same quality standards as the UI app.
- Automatic operation is expected: the pod started from the UI transcribes every batch that is
  ready for transcription, saves the output to persistent storage as a backup, uploads the
  output per batch, then stops the pod.
- In the first test period the pod is not stopped automatically; the architect will SSH in.
- Base images suggested by the Runpod UI (to be evaluated):
  - `runpod/pytorch:2.1.0-cu1281-torch280-ubuntu2404`
  - `runpod/pytorch:2.1.0-cu1290-torch280-ubuntu2404`
  - `runpod/pytorch:2.1.0-cu1300-torch280-ubuntu2404`
  - `runpod/pytorch:2.1.0-cu1320-torch280-ubuntu2404`

### Failure handling

- A file that cannot be transcribed, or has empty output, must not block the process: return an
  empty file, flag it in the UI, ideally with a retry button that puts it into a separate batch.
- The Runpod pod must never be left running, not even after a major failure.
- A failed transcription or an interrupted batch must not harm the successful transcriptions.
- Batch handling should be smarter than "download all audio files, then upload everything".

## Architecture and code quality

- Project language: English (code, docs, commits, PR texts, product questions).
- Very high code quality standard.
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
- Managed with uv; Python 3.11–3.14, whichever fits better.
- Lint setup taken from
  https://github.com/MarkFazekas/ApplicantFilterSystemO/tree/main/manager_backend : flake8 +
  ruff + mypy. Ruff does not cover every flake8 plugin, so the full `.flake8` and ruff configs
  are carried over, adapted to this project.
- Conventional commits.
- On push to the main branch, build executables for Windows, macOS and Ubuntu.
- A Claude hook runs a code review in a sub-agent before the session returns with code or any
  other change.

## Documentation

- Documentation lives in `docs/`.
- Human input (what the architect/product says) goes to `docs/human/`; what Claude produces
  from inputs and discoveries goes to `docs/ai/`.
- Keep information that is useful later; do not write a history book.
- `CLAUDE.md`, `README.md` and `.gitignore` are part of the repo. Docs handling rules are in
  `CLAUDE.md`.
