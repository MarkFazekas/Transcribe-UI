# STTCLI extraction (https://github.com/MarkFazekas/STTCLI, HEAD a2dd4c6, read-only analysis)

Files: README.md (169 lines), batch.sh (273), pyproject.toml (19), .python-version ("3.11"), uv.lock (3662), .gitignore (94, JetBrains/Python boilerplate). No Dockerfile, no requirements.txt, no tests, no CI (.github absent), no linter config, no Python source files at all. The project is: README + one bash script + a uv-locked whisperx dependency.

## History (12 commits, 2026-10-28..30, all by Márk)
Initial -> "Usage" x2 -> "GPU Usage" (adds batch.sh) -> "Final GPU usage" x3 -> "Add optional single-process mode" (--all) -> rename TTSCLI->STTCLI -> "Require explicit batch mode and language flags" -> "Don't fail on empty SRT." (HEAD).
Language was initially implicit; now required. Last commit replaced `exit 1` in the --all missing-SRT check with 12 duplicated echo lines (see Gotchas).

## Architecture / flow
No Python code. Two sides:
- Local (CPU, Linux/WSL, uv): README:20-23 manual ffmpeg audio extraction, then optional local CPU whisperx run (README:90-120). Local side does NOT upload to R2 in this repo; the README never shows how audio gets into the bucket `audio/` prefix (inferred: user uploads manually/with aws cli).
- Runpod (GPU): README:25-88 setup, then `batch.sh`: verify GPU -> `aws s3 sync` audio/ down and subtitles/ down (resume) -> transcribe missing with whisperx -> upload each SRT to R2 `subtitles/`.
No metadata/manifest file; state = presence of non-empty `output/<stem>.srt`.

## ffmpeg (README only; batch.sh calls no ffmpeg, only checks it exists, batch.sh:62)
README:21 and README:156:
    ffmpeg -i video2271575718.mp4 -map 0:a:0 -c:a copy video2271575718.m4a
    ffmpeg -i input.mp4 -map 0:a:0 -c:a copy output.m4a
- Always stream copy first audio stream (`-map 0:a:0 -c:a copy`) into a hard-coded `.m4a`. There is NO per-codec container/extension logic; it assumes source audio is AAC (inferred). Non-AAC (e.g. AC3/Opus/MP3) into .m4a would fail or be unsuitable (inferred, not documented).
- No -vn, -y, -ac, -ar; no subtitle extraction; no resampling (whisperx/ffmpeg decodes to 16k itself, inferred).
- batch.sh accepts only `audio/*.m4a` and `audio/*.mp3` (batch.sh:117). Duplicate stems (a.m4a + a.mp3) abort (batch.sh:124-136).

## Runpod setup (README:25-60, verbatim)
Select: runpod/pytorch:1.5.0-cu1281-torch280-ubuntu2404
(No disk sizes, ports, ssh, env-var UI settings, or start command are specified anywhere. GPU type not specified. Container disk/volume: not stated.)
    apt-get update
    apt-get install -y git curl ffmpeg
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    cd /root
    git clone https://github.com/MarkFazekas/STTCLI.git
    cd STTCLI
    uv python install 3.11
    sed -i \
      -e 's/pytorch-cpu/pytorch-cu128/g' \
      -e 's|https://download.pytorch.org/whl/cpu|https://download.pytorch.org/whl/cu128|g' \
      pyproject.toml
    uv lock
    uv sync --locked
    uv pip install hf_transfer
    uv tool install awscli
Env (README:54-60):
    export AWS_ACCESS_KEY_ID="YOUR_ACCESS_KEY" \
           AWS_SECRET_ACCESS_KEY="YOUR_SECRET_KEY" \
           AWS_DEFAULT_REGION="auto" \
           R2_ENDPOINT="https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com" \
           R2_BUCKET="YOUR_BUCKET"
Run (README:66-86):
    cd /root/STTCLI; chmod +x batch.sh; set -o pipefail
    ./batch.sh --per-file --language hu 2>&1 | tee batch.log     # or en
    ./batch.sh --all --language hu 2>&1 | tee batch.log
Notes: the sed switches the CPU torch index to cu128 in pyproject, then `uv lock` re-resolves (so the committed uv.lock is CPU-only and not used as-is on Runpod). `hf_transfer` is installed but HF_HUB_ENABLE_HF_TRANSFER is never set (inferred: the install is therefore likely no-op). `uv tool install awscli` needs ~/.local/bin on PATH (set in same shell session).

## batch.sh (verbatim key parts)
Config (11-19): INPUT_PREFIX="audio" OUTPUT_PREFIX="subtitles" MODEL="large-v3" BATCH_SIZE=16 COMPUTE_TYPE="float16"
CLI (21-42): `Usage: $0 (--per-file|--all) --language LANG`; both required; exits 2 otherwise. `set -euo pipefail`; `cd "$(dirname "${BASH_SOURCE[0]}")"`.
Env validation (48-60): requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET; `export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"`; requires cmds aws, uv, ffmpeg. `mkdir -p audio output .work`.
GPU check (75-88): `uv run --no-sync python - <<'PY'` asserts torch.cuda.is_available() and "float16" in ctranslate2.get_supported_compute_types("cuda"); prints GPU name, torch, CUDA, compute types.
Sync (96-109):
    aws s3 sync "s3://${R2_BUCKET}/${INPUT_PREFIX}/" ./audio/ --endpoint-url "$R2_ENDPOINT" --only-show-errors
    aws s3 sync "s3://${R2_BUCKET}/${OUTPUT_PREFIX}/" ./output/ --endpoint-url "$R2_ENDPOINT" --only-show-errors
Transcribe (144-158):
    transcribe_files() { local target_dir="$1"; shift
        TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 \
        uv run --no-sync whisperx "$@" \
            --device cuda --compute_type "$COMPUTE_TYPE" --model "$MODEL" \
            --language "$LANGUAGE" --batch_size "$BATCH_SIZE" \
            --output_format srt --output_dir "$target_dir" --print_progress True }
--all mode (163-207): collect files lacking non-empty output/<stem>.srt, run ONE whisperx process over all (models loaded once) into mktemp dir ./.work/all.XXXXXX, then check each SRT, then `mv` into output/.
--per-file mode (209-259 loop): skip if output/<stem>.srt non-empty; else transcribe into ./.work/<stem>.XXXXXX, error+exit 1 if SRT missing/empty (235-238), `mv` into output/.
Upload (250-256, both modes, every file, always, even if skipped):
    aws s3 cp "$result" "s3://${R2_BUCKET}/${OUTPUT_PREFIX}/${name}.srt" --endpoint-url "$R2_ENDPOINT" --only-show-errors
Summary prints counts and `Results: s3://${R2_BUCKET}/${OUTPUT_PREFIX}/`.

## Whisper engine / parameters
WhisperX (README:148 `uv add whisperx`; lock whisperx 3.7.9, which pulls faster-whisper 1.2.1, ctranslate2 4.8.4, pyannote-audio 3.4.0, transformers 4.57.6, nltk 3.10.4, onnxruntime 1.31.1). Backend = faster-whisper/CTranslate2 (inferred from deps; GPU check uses ctranslate2).
- GPU (batch.sh): --model large-v3, --compute_type float16, --batch_size 16, --device cuda, --language <required>, --output_format srt, --print_progress True.
- CPU high (README:93-103): float32, large-v3, --threads 8, --batch_size 4. CPU low (README:109-119): int8, large-v3-turbo, --threads 6, --batch_size 4. Both `--device cpu`, `--output_format srt`, `--output_dir ./output`.
- NOT set (whisperx defaults apply, not verified): beam size, VAD options/method, --align_model, --no_align, diarization, --hf_token, --task, initial prompt, temperature. Alignment runs by default (whisperx default; the --all comment says "retains all transcription results until alignment/output", README:88). HF token/diarization not used.
- Output: SRT only. Naming: whisperx writes `<input-stem>.srt`; final path `output/<stem>.srt`, uploaded to `subtitles/<stem>.srt`. NO language suffix (not movie.en.srt); the language is not in the filename, so running hu then en for the same stems would see existing SRT and SKIP (gotcha, inferred from the skip logic at 167/220: skip keys on stem only).
- Env quirk: `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` on every whisperx call (workaround for torch>=2.6 weights_only default breaking pyannote/whisperx checkpoint loading; reason inferred, no comment in repo).

## Transfer
Cloudflare R2 via S3 API with awscli (`uv tool install awscli`), `--endpoint-url`, region "auto". Layout: `s3://$R2_BUCKET/audio/*.{m4a,mp3}` in; `s3://$R2_BUCKET/subtitles/<stem>.srt` out. No metadata files. No rsync/scp/ssh. Credentials via env vars.

## Subtitle detection logic
None. No detection of existing subtitles (.en.srt/.hu.srt) next to videos anywhere. Only "already transcribed" detection = non-empty output/<stem>.srt (restored from R2 `subtitles/`).

## Gotchas / workarounds / failure handling
- batch.sh:180-196 (HEAD): `test -s ... || { echo ERROR x12 }` — no `exit`, so an empty SRT no longer fails. The echo is duplicated 12 times (commit artifact). Subsequent `mv` (199-202) under `set -e` still fails if the file is entirely missing, but an empty SRT is moved and later uploaded (and `test -s "$result"` at line 219 then aborts the run under set -e in --all mode, since it is the bare statement). Net: empty SRT in --all mode is reported, moved to output/, and then killed at line 219 (inferred from bash semantics; not run). Per-file mode still exits 1 on empty SRT (235-238).
- --all uploads only after the full run; interrupted run loses all progress for that batch (README:88). Per-file mode is resumable.
- Output filename collisions guarded (stem duplicate check).
- Upload is always re-done, also for skipped files.
- Atomic mv from temp dir; `.work/` temp dirs may linger on failure; `.work`, `audio`, `output` are not in .gitignore as far as checked (inferred; .gitignore seen only partially: JetBrains boilerplate + Python; grep for these not done).
- README duplicates "# Usage" and "First install" sections (older notes). README says `uv sync --locked` for CPU; GPU path regenerates the lock.
- No TODO/FIXME comments in repo.

## Dependencies
pyproject.toml: name sttcli 0.1.0; requires-python >=3.11; deps torch==2.8.0, torchaudio==2.8.0, torchvision==0.23.0, whisperx>=3.7.9; uv sources map those three to index `pytorch-cpu` = https://download.pytorch.org/whl/cpu. .python-version 3.11. Lock revision 3, whisperx 3.7.9, torch 2.8.0(+cpu). Runpod image torch280 matches torch 2.8.0; cu128.
System: ffmpeg, git, curl, uv, awscli.

## Lint / test / CI
None.
