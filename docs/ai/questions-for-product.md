# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: Q36).

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

### Q35. Deleting a job's subtitles from the cloud when several computers download them

Since Q28, a job's subtitles can be downloaded by the computer that uploaded it and also by
another one (for example the cloud supervisor's), each keeping its own history. Deleting
everything from the cloud on one computer takes the subtitles away from the other, and today a
computer only knows whether it downloaded them itself.

Proposed default: subtitles are never deleted from the cloud automatically, and nothing else
changes, except that the "delete everything" confirmation adds: "Other computers that share the
cloud storage may still need these subtitles."

Alternative: each computer that downloads a job notes this in the cloud storage (with a
computer name set in Settings); "delete everything" lists them and asks a second time while the
uploading computer has not downloaded the job.
