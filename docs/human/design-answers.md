# Design answers (human input)

The architect's answers to `docs/ai/questions-for-architect.md` (A#) and, acting for product, to
`docs/ai/questions-for-product.md` (Q#), translated from Hungarian (2026-11-26). The derived
decisions are in the "Decided" sections of those files.

## Architect

- **A1:** "As far as I remember, when the language changes, Whisper unloads and loads some
  dictionary belonging to the language. So an en hu en file order is slower than en en hu."
- **A2:** "I don't understand why it would be good to worsen the output quality by dropping the
  word timestamp alignment. As far as I understand, the proposal worsens output quality, and
  that comes first."
- **A3:** "I think I may have logged in over SSH through Runpod's system, and no SSH server is
  needed for it. Adjust the rest of A3 to the above."
- **A4:** good.
- **A5:** "I would put the model cache into the Docker image so the whole thing starts faster."
- **A6:** "The limit should be 2 hours."
- **A7:** Runpod secrets are good.
- **A8:** good. **A9:** OK. **A11:** good. **A16:** good. **A17:** yes.
- **A10:** "With SQLite, SQLAlchemy and Alembic."
- **A12:** not answered yet.
- **A13:** "There should also be a GH Release on main."
- **A14:** "Fine. Constants go per folder / app / sub-app into a constants.py with a roughly
  one-line explanation of their purpose, not with an underscore at the top of the file.
  Separate models.py for the SQL models, classes.py for the Pydantic models, a separate
  migrations folder for migrations. A Django project structure is appealing."
- **A15:** "If a Runpod test is needed, then only that runs, single-threaded, while I am here;
  you don't just start anything."

## Product

- **Q1:** "After the file selection there should be a clarifying step: soundtracks listed per
  file, and where there is more than one soundtrack, one can choose which one. The files with
  several soundtracks should be in one summary step. There, too, one can filter by language and
  choose per folder: one language for one folder, then another language for another folder,
  as in the main filter."
- **Q2:** "The first part of the clarifying step: these files have a subtitle; should they be
  transcribed, or is extracting the subtitle enough? (Language filter, folder filter choice,
  etc., as in the main batch and the soundtrack listing.)"
- **Q3, Q4:** the default is good. **Q5:** SRT.
- **Q6:** "Practically this state can only exist if it got transcribed while the batch was
  being started. Don't spend much effort on this. They should be listed, yes; default skip is
  good; selectable overwrite is good."
- **Q7:** "The app should show them, and the default behaviour for them is A_movie.hu.srt,
  B_movie.hu.srt. Forget the numbering."
- **Q8:** "It should be selectable which folder names are kept; the default behaviour is that X
  folder names, counted from the file, are kept. C:\A\B\C\D\e.mkv keeping 2 folders →
  C_D_e.hu.srt."
- **Q9:** "Default, plus if another language is enabled in the global settings, it is in the
  list too, obviously."
- **Q10:** "There should be a global app-level setting where the languages supported by the
  transcription can be turned on. HU and EN on; if someone turns on DE, it appears in the
  language chooser too, obviously."
- **Q11:** "How could the language be detected?" (question back; answer in the Q11 text)
- **Q12, Q13, Q16, Q18, Q19, Q20, Q21:** good / default. **Q15:** "Sure."
- **Q14:** "The option to clean up the cloud should be available after the job and also when
  the job is opened. It should be possible to delete only the audio files, or audio + subtitles
  (everything). Obviously, if there is no audio any more, deleting everything must not raise
  an error. So the deletion option is not only after the job: it is a maintenance option
  whenever the job is opened."
- **Q17:** "The user can be asked how long to wait for a machine; default 1 hour, any time."

## Process

- "No '🤖 Generated with Claude Code' line in the PR." (recorded in `working-agreements.md`)
