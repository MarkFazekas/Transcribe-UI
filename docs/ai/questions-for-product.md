# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q36).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q33. Main window layout

Proposed default: one main window with four pages in a left-side navigation:

- New job: choose a folder, the file tree with filters and selection, the target languages,
  Create job.
- Jobs: the job history (with "show hidden"), and per job its files and statuses, the review
  step, upload, download, retry and cloud cleanup.
- Cloud: start transcription (with the cost estimate and the wait for a machine), the current
  run (waiting, starting, transcribing which job and file, elapsed time, cost so far), the last
  runs, and the list of the app's cloud machines.
- Settings: languages, file types, hidden files, ffmpeg, default download options, accounts
  (R2 and Runpod keys), cloud setup, about.

### Q34. Stopping the cloud machine while it transcribes

The requirements let the user cancel while waiting for a free cloud machine. The app can also
stop a machine that already started or is transcribing.

Proposed default: the Cloud page offers "Cancel" while waiting and "Stop" while starting or
transcribing, both with a confirmation. Stopping keeps every subtitle already finished; the
files not done yet wait for the next start of transcription.

### Q35. Deleting a job's subtitles from the cloud when several computers download them

Since Q28, a job's subtitles can be downloaded by the computer that uploaded it and also by
another one (for example the cloud supervisor's), each keeping its own history. Deleting
everything from the cloud on one computer takes the subtitles away from the other, and today a
computer only knows whether it downloaded them itself.

Proposed default: subtitles are never deleted from the cloud automatically. Each computer that
downloads a job notes this in the cloud storage. A computer is shown by the name set in its
Settings (default: the computer's network name). "Delete everything" lists which computers
have downloaded the job; if the computer that uploaded the job has not downloaded it yet, the
confirmation says so and asks a second time (as it already does when this computer has not
downloaded it). The uploading computer's own cleanup needs no extra confirmation for other
computers.
