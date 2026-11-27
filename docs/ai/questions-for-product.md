# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q29).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q22. Line length of Chinese and Japanese subtitles

Subtitle lines hold at most 42 characters. Chinese and Japanese characters are about twice as
wide on screen as Latin letters, so a 42-character line in these languages is roughly twice as
wide as an English one and may not fit the screen.

Proposed default: keep 42 characters for every language for now (Hungarian and English are the
main languages); revisit if Chinese or Japanese subtitles are actually used.

### Q23. Subtitles "next to the original file" when that folder is gone

The job remembers where each original file was. If, by the time the subtitles are downloaded,
the file's folder was moved, renamed or deleted, the subtitle cannot be put next to it.

Proposed default: the download plan lists these files with the reason "original folder not
found" and skips them; every other subtitle is placed. The user can download the skipped ones
again with another option (for example into a chosen folder; see Q25).

### Q24. Downloading finished subtitles before the whole job is done

The cloud machine finishes a job's files one by one. The phases in the requirements allow the
download only once the whole job is transcribed. If a run stops before the end (the machine
fails, the time limit is reached, the user cancels), nothing of that job could be downloaded
until a later run finishes it, even the files that are already done.

Proposed default: subtitles that are finished can be downloaded at any time, also while the job
is waiting or being transcribed. The download plan lists the files still without a subtitle as
"not finished yet" and skips them. The job counts as downloaded once it is fully transcribed and
every file has been through a download (placed, or skipped as an existing file, a missing
folder or an empty result), whether in one download or several. The next start of
transcription does the missing files.

### Q25. Downloading a job's subtitles again

After a download, the subtitles stay in the cloud until the user cleans up.

Proposed default: a downloaded job can be downloaded again any number of times, with any
placement option, as long as its subtitles are still in the cloud (not after "delete
everything"; subtitles taken from inside the video files are kept on the computer and can be
placed again even after that). Files already in place are listed as existing and skipped
unless overwrite is chosen. This is also how the files skipped under Q23 are placed later.

### Q26. Abandoning a job before it is uploaded

A job can be deleted while its files are being selected. Once the review step started, or the
upload started, there is no way out: a half-uploaded job keeps its audio in the cloud.

Proposed default: a job in the review step can still be deleted (nothing is in the cloud yet;
it disappears from the history). A job whose upload has started can be abandoned with the cloud
cleanup "delete everything": the uploaded audio and unfinished uploads are deleted, and the job
stays in the history as cleaned up.

### Q27. A job that finishes uploading while the cloud machine is waiting or working

Starting transcription sends every uploaded job, with a cost estimate and a time limit worked
out for them. A job whose upload completes after the start would otherwise be picked up by the
same machine if it is still working: beyond the confirmed estimate, and possibly cut by the
time limit.

Proposed default: such a job completes its upload but waits ("uploaded, waits for the current
run"); it is not given to the running machine and becomes ready for the next start as soon as
the current run ends.

### Q28. Jobs in the cloud that this computer does not know

The cloud machine transcribes every job it finds in the cloud storage, including jobs uploaded
from another computer or by an earlier installation whose local history is gone. This computer
has no history for them, so it cannot place their subtitles.

Proposed default: the start confirmation lists these jobs (job id, number of files, audio
length) and includes them in the audio length and cost estimate. The user can delete such a job
from the cloud from that list (with one confirmation) before starting.
