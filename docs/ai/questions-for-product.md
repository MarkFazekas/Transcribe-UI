# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions move to the "Decided" section with the decision; the ID stays stable.

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

**Q1. Which soundtrack is transcribed when a video has several?**
Some videos contain several soundtracks, for example a Hungarian dub and the original English.
Default: for each language, use the soundtrack marked with that language; otherwise the
video's main soundtrack. The chosen soundtrack is shown for each file before uploading.

**Q2. Should subtitles stored inside a video file count as "has subtitle"?**
Some video files carry subtitles inside them. Checking for these means opening every file,
which makes listing a large drive much slower.
Default: only separate subtitle files next to the video count for now. Checking inside videos
can be added later as an optional, slower scan.

**Q3. Which subtitle files are recognised, and what about `movie.srt` without a language?**
Default: subtitle files with the same name as the video, with or without a language in the
name, for example `movie.srt`, `movie.hu.srt`, `movie.hun.srt`, `movie.hungarian.srt`,
`movie.en.forced.srt` (also `.vtt`, `.ass`, `.ssa`). A file without a language, like
`movie.srt`, counts as "subtitle in an unknown language": it matches "has any subtitle" but not
"has Hungarian".

**Q4. How are new subtitle files named?**
Default: the video's name plus a two-letter language code, for example `movie.hu.srt` and
`movie.en.srt`. Media players such as VLC, Plex and Jellyfin recognise this pattern.

**Q5. Which subtitle formats are needed?**
Default: SRT only (the most widely supported). WebVTT or plain text can be added later.

**Q6. What happens if a subtitle file with the same name already exists?**
Default: nothing is overwritten silently. Before downloading, the app lists these files; by
default they are skipped, and the user can choose "overwrite" for that download.

**Q7. What happens when two subtitles would get the same name in one folder?**
Example: `A/movie.mkv` and `B/movie.mkv` both become `movie.hu.srt` in a single folder.
Default: the app shows these before downloading and numbers the later ones (`movie.hu.2.srt`).
The user can switch to the "keep folder names" option (Q8) instead.

**Q8. In "one folder, keep folder names joined with `_`", which folder names are kept?**
Default: the folders below the folder the user opened, joined with `_`. Example: opened `D:\A`,
file `D:\A\C\x\movie.mkv` → `C_x_movie.hu.srt`. The user can choose how many folder levels to
keep.

**Q9. Should the subtitle filter go beyond "any / English / Hungarian / both"?**
Default: yes. Pick any set of languages and a rule: "has all of them", "has any of them", or
"is missing at least one of them". This covers "files with a Hungarian subtitle but no English
one".

**Q10. Which languages can be chosen?**
Default: every language the transcription supports (about 99), with Hungarian and English at
the top and selected by default, plus a search box.

**Q11. Should the app detect the spoken language automatically?**
Default: no. The user always chooses the spoken language. A chosen language gives better
results and avoids wrong guesses, for example on music intros.

**Q12. How are failed or empty transcriptions shown?**
A file can fail (for example a damaged file) or contain no speech (for example only music).
The brief says these return an empty file. Default: the app marks these files in the job view
with the reason ("no speech found" or "failed: …") and a Retry button. It does **not** put empty
subtitle files next to the videos unless the user asks for it.

**Q13. How does Retry work?**
Default: Retry starts a new job with only the selected marked files and the same language. The
audio already in the cloud is reused, so nothing is uploaded again. The retry uses a slower,
more careful transcription setting.

**Q14. What does the final cleanup delete?**
Default: after the subtitles are downloaded, the app asks once per job: "Delete this job's
uploaded audio and subtitles from cloud storage?" Confirming deletes everything of that job in
the cloud. The job stays in the app's history (see Q21). Nothing is deleted from the cloud
without this confirmation.

**Q15. Can a file be transcribed again into a language it already has a subtitle for?**
Default: yes, but the app warns before the job is created. When a whole folder is selected
while the filter "is missing <language>" is on, such files are not selected.

**Q16. What does the user see after pressing "Start transcription"?**
Default: one button sends every job that has finished uploading. The app then shows the state:
waiting for a free cloud machine, starting, transcribing (which job and file, elapsed time,
cost so far), finished, or failed. While waiting for a machine, the user can cancel.

**Q17. How long may the app wait for a free cloud machine, and may it start at night?**
The required machine type is not always free. The cloud provider can keep the request waiting
and start the job as soon as a machine frees up, even when the app is closed. Billing starts
when the machine starts (about $0.27 per hour; a typical job is expected to take well under a
few hours), so a job may start and be billed at night.
Default: wait up to 12 hours, at any time of day; after that, give up and tell the user.
Alternatives: a shorter wait, or allowed start hours only (for example 08:00–22:00).

**Q18. Which files are listed?**
Default: all common video and audio file types (MP4, MKV, MOV, AVI, WebM, MP3, M4A, WAV, FLAC,
OGG, Opus and similar). The exact list can be adjusted later in the settings.

**Q19. Are hidden and system folders listed? Are shortcuts followed?**
Default: hidden and system folders are not listed (a setting can include them); folder
shortcuts/links are not followed, so the same file never appears twice.

**Q20. Should the user confirm the expected cost or set a spending limit?**
Default: before starting, the app shows the total audio length and an estimated cost range and
asks for confirmation. No separate spending limit in the app; the cloud account's own spending
limit applies.

**Q21. How long is the job history kept in the app?**
Default: forever, with an option to hide finished and cleaned-up jobs. History only contains
file names, paths and statuses (no audio).

## Decided

(none yet)
