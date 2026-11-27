# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q35).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q28. Jobs in the cloud that this computer does not know

The cloud machine transcribes every job it finds in the cloud storage, including jobs uploaded
from another computer or by an earlier installation whose local history is gone. This computer
has no history for them, so it cannot place their subtitles.

Proposed default: the start confirmation lists these jobs (job id, number of files, audio
length) and includes them in the audio length and cost estimate. The user can delete such a job
from the cloud from that list (with one confirmation) before starting.

### Q29. Several cloud backup volumes with the app's name

The app keeps one backup volume on Runpod for the cloud machine's result backups. If Runpod ever
holds two or more with the app's name (a setup interrupted at the wrong moment, or one created by
hand), the app cannot tell which holds backups that matter, so it never deletes one itself and
refuses to start a cloud machine until only one is left.

Proposed default: the cloud setup and the start show an error listing the volumes' ids; the user
deletes the unwanted volume in the Runpod console (Storage page) and runs the setup again.

### Q30. Closing the app while work is in progress

The app may be converting and uploading files, downloading subtitles or cleaning up the cloud
when the user closes the window, or a cloud machine may be waiting or transcribing.

Proposed default: if a step is running, the app asks "Stop and close? The work continues from
where it stopped the next time you start it." On yes, it stops the step cleanly (this can take a
few seconds; a "Quit now" button ends at once, still without losing finished work). If a cloud
machine is waiting or working, the close question also says that the machine keeps working
without the app, stops itself when the work is done or at its time limit, and that the app
shows its results the next time it starts. Closing never cancels the cloud machine; cancelling
is a separate button on the Cloud page.

### Q31. Steps the app starts on its own

Several steps could follow each other automatically: the upload after the review step, the start
of transcription after an upload, the download after transcription, and continuing an
interrupted upload or download when the app starts again.

Proposed default: the app starts none of these on its own; each is one button (Upload at the end
of the review step, Start transcription on the Cloud page, Download on the job page, Resume on an
interrupted job). The app only checks the cloud machines at start-up and follows a running one,
and it shows clearly which jobs are interrupted or have subtitles ready.

### Q32. Language of the app's texts

The project is in English, the users are Hungarian and English speakers.

Proposed default: the app's texts are in English only for now; they are kept together per screen
so a Hungarian translation can be added later.

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
