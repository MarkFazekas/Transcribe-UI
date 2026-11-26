# Questions for product

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions move to the "Decided" section with the decision; the ID stays stable.

Terms used below: a **job** is one run of transcriptions that the user starts; the **cloud
machine** is the rented graphics-card computer that does the transcription.

## Open

**Q11. Should the app detect the spoken language automatically?**
Ways to detect it:
- Video files often label each soundtrack with a language. This costs nothing, but the label is
  often missing or wrong. The review step (Q1) already shows it.
- Whisper can guess the language from the first 30 seconds of speech, with a confidence value.
  On the cloud machine this takes about a second per file.
- A small language model on the user's computer could guess before uploading (a few seconds per
  file, and the app download grows by about 100 MB).
Default: the user still chooses the language. The cloud machine also checks every file and
marks a mismatch in the results, for example "Hungarian chosen, sounds English (97 %)", with a
Retry in another language. Nothing is added to the desktop app.

## Decided

**Q1. Soundtrack choice.** After file selection, a review step lists the files with more than
one soundtrack in one summary view, with each track's language. The user picks the track per
file. The view has the same language and folder filters as the main view, so one folder can get
one language's track and another folder another language's.

**Q2. Subtitles inside video files.** The "has subtitle" filter in the main view counts only
separate subtitle files. The first part of the review step lists the selected files that carry
a subtitle inside the file and asks per file (with the same language/folder filters):
transcribe, or just extract the existing subtitle. Picture-based subtitles (DVD/Blu-ray) cannot
be extracted as text, so for those only transcription is offered.

**Q3. Recognised subtitle files.** Default.

**Q4. New subtitle names.** Default: `movie.hu.srt`.

**Q5. Formats.** SRT only.

**Q6. Existing subtitle with the same name.** Listed before download; skipped by default;
overwrite selectable. Kept low effort, as this is uncommon.

**Q7. Same name in one folder.** The app shows these files; by default each of them gets its
folder name as prefix: `A_movie.hu.srt`, `B_movie.hu.srt` (more folder levels are added if the
folder names match too). No numbering.

**Q8. Flat folder with folder names.** The user chooses which folder names are kept. Default:
the X folders closest to the file. Example: `C:\A\B\C\D\e.mkv` with 2 folders kept →
`C_D_e.hu.srt`. X starts at 1 (my proposal; product did not specify it).

**Q9. Subtitle filter.** Default (all / any / missing at least one of a chosen language set).
The list offers every language enabled in the settings (Q10).

**Q10. Languages.** A global setting lists every language the transcription supports, each with
an on/off switch. Hungarian and English are on by default. Enabled languages appear in the
language choosers and filters.

**Q12. Failed or empty results.** Default.

**Q13. Retry.** Default.

**Q14. Cloud cleanup.** Available after download and at any time from the job's page as a
maintenance action: "delete audio only" or "delete everything (audio and subtitles)". Deleting
something already gone is not an error.

**Q15. Re-transcribing an existing language.** Default.

**Q16. Progress after "Start transcription".** Default.

**Q17. Waiting for a cloud machine.** The app asks at every start how long to wait; default 1
hour; any time of day.

**Q18. Listed file types.** Default.

**Q19. Hidden folders and shortcuts.** Default.

**Q20. Cost confirmation.** Default.

**Q21. Job history.** Default.
