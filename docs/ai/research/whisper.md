# Whisper engine research for Runpod A5000 subtitle worker (as of 2026-11-26)

Legend: [V] verified from a fetched source (URL given); [I] inference / general knowledge, not verified here.
WhisperX 3.8.6 requirements [V: https://pypi.org/pypi/whisperx/json]: Python >=3.10,<3.14; torch~=2.8.0, torchaudio~=2.8.0, torchvision~=0.23.0, faster-whisper>=1.2.0, ctranslate2>=4.5.0, pyannote-audio>=4.0.0, transformers>=4.48.0, huggingface-hub<1.0.0, nltk, pandas, omegaconf, torchcodec>=0.6,<0.8.
Baseline (from docs/ai/research/reference-sttcli.md): STTCLI used WhisperX 3.7.9 (faster-whisper 1.2.1 / ctranslate2 4.8.4), large-v3, float16, batch_size 16, SRT, runpod/pytorch cu128 torch 2.8.

> Superseded in part: the architect chose WhisperX with alignment (A2) over faster-whisper
> alone; the facts below remain valid.

## TL;DR recommendation
- Engine: faster-whisper (CTranslate2) used directly, not via WhisperX. Same decoding core as the baseline, minus torch/pyannote/wav2vec2 weight. Use BatchedInferencePipeline (batch_size 8-16) for throughput, or plain WhisperModel.transcribe for max robustness (see sec. 4 trade-offs).
- Model: large-v3 (Systran/faster-whisper-large-v3, fp16, 3.09 GB) as default. Hungarian evidence favours large-v3 over large-v3-turbo (modest gap). Allow turbo as opt-in speed profile for high-resource languages.
- Language is a per-call decode parameter: one process, one loaded model, files in any language sequentially. "One batch per language" is NOT technically required (sec. 3). It is only a convenient grouping (e.g. one `language` setting per job).
- Keep WhisperX only if you need wav2vec2 forced-alignment word timing or diarization. Hungarian alignment model exists in WhisperX (see 1). For SRT, Whisper's own word timestamps + sane splitting are usually enough [I].

## 1. Engine comparison
| Engine | Status 2026 | Torch needed | Notes |
|---|---|---|---|
| openai-whisper | Last GitHub release v20250625 [V: https://api.github.com/repos/openai/whisper/releases] ; effectively slow-moving | yes | Slowest; sequential; has hallucination_silence_threshold; no built-in VAD. Not recommended. |
| faster-whisper | PyPI 1.2.1 released 2025-10-31 [V: https://pypi.org/pypi/faster-whisper/json]; master commits active through 2026-11-12 (batched transcription fixes, clip_timestamps validation, unsupported-language error) [V: https://api.github.com/repos/SYSTRAN/faster-whisper/commits]. No PyPI release in a year though -> consider pinning a git commit only if you need those fixes [I]. MIT | NO. deps: ctranslate2 (>=4,<5), av>=11, onnxruntime, tokenizers, huggingface-hub [V] | Silero VAD v6 via ONNX (CPU) [V: vad.py]. PyAV bundles FFmpeg [V: README https://github.com/SYSTRAN/faster-whisper]. |
| CTranslate2 | v4.9.1 (2026-11-22), 4.9.0 (11-10), 4.8.5 (11-02); active [V: https://github.com/OpenNMT/CTranslate2/releases] | n/a | 4.9.1 adds Blackwell targets to CUDA 12 builds. |
| WhisperX | 3.8.6 (2026-05-25), 3.8.7rc1 (2026-06-26) [V: https://api.github.com/repos/m-bain/whisperX/releases]; BSD-2 | YES (torch, torchaudio, pyannote, wav2vec2, nltk) | Batched faster-whisper + pyannote VAD + wav2vec2 forced alignment (+diarization). Hungarian alignment available: DEFAULT_ALIGN_MODELS_HF["hu"] = jonatasgrosman/wav2vec2-large-xlsr-53-hungarian [V: https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py]. README: batched transcription runs without timestamps, output may differ from OpenAI Whisper [V: https://github.com/m-bain/whisperX]. Heavier image, more failure surface; baseline is older than latest (3.7.9 vs 3.8.x). |
| insanely-fast-whisper / HF transformers pipeline | not researched in depth | yes | Fast with flash-attn/SDPA but chunked long-form (30s chunks stitched), weaker timestamps, no VAD; not recommended for subtitles [I]. |
| whisper.cpp | v1.9.6 2026-11-23, very active, has VAD [V: https://api.github.com/repos/ggml-org/whisper.cpp/releases] | no | Strong on Apple/CPU; on NVIDIA slower than CT2 batched [I; blog benchmarks are SEO-grade and conflicting: https://dev.to/tildalice/whispercpp-vs-faster-whisper-wer-accuracy-test-on-librispeech-167c]. Needs ggml model conversion and its own audio input (ffmpeg). Not recommended here. |
| NVIDIA Parakeet-TDT-0.6B-v3 | Supports 25 European languages INCLUDING Hungarian (hu); FLEURS hu WER 15.72; word/segment/char timestamps; CC-BY-4.0; long audio up to 24 min full attention (A100 80GB) or 3 h with local attention; NeMo 2.4 runtime, Linux, Ampere supported [V: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3] | yes + NeMo (heavy) | Much faster than Whisper, but only 25 languages (brief says "other Whisper-supported languages" -> ~99 langs). Whisper large-v3 FLEURS hu ~14.6-19.8 depending on normalisation [V, sec 2], so accuracy is comparable-ish, not clearly better. No subtitle-oriented segmenting, no VAD built in [I]. Possible future fast path for the 25 languages; not the primary engine. |
| NVIDIA Canary-1B-v2 | 25 European languages, adds translation [V: https://huggingface.co/nvidia/canary-1b-v2 via search; Hungarian in full Canary list NOT confirmed] | yes + NeMo | Same tradeoffs as Parakeet. |

Newer Whisper-family: no new open-weight OpenAI Whisper (no "large-v4") found in 2026; OpenAI's newer models (gpt-4o-transcribe, GPT-Realtime-Whisper) are closed API [V search: https://thenextweb.com/news/openai-gpt-realtime-2-voice-models]. Other open ASR mentioned: Cohere Transcribe 2B (Apache-2.0), AI2 OLMoASR [search result only, not evaluated for Hungarian].

## 2. Model choice
- large-v3: Systran/faster-whisper-large-v3, model.bin 3.09 GB [V: https://huggingface.co/Systran/faster-whisper-large-v3/tree/main].
- large-v3-turbo: ~809M params, decoder 32 -> 4 layers, MIT; card says "strong ASR results in ~10 languages", can hallucinate/repeat [V: https://huggingface.co/openai/whisper-large-v3-turbo]. CT2: deepdml/faster-whisper-large-v3-turbo-ct2, 1.62 GB [V: https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/tree/main] (faster-whisper's built-in alias "turbo" also exists [V README]).
- Hungarian zero-shot evidence (community table by sarpba, mixed batch sizes, author warns hallucinations skew some rows) [V: https://huggingface.co/sarpba/whisper-teszt-eredmenyek]:
  - FLEURS hu: large-v3 19.77 raw / 14.62 normalized; turbo 21.09 / 16.05.
  - KTH hu: large-v3 27.25 / 16.29; turbo 27.61 / 17.28.
  -> large-v3 better by ~1-1.5 points normalized WER; throughput of turbo is several times higher [I]. Turbo is not catastrophically worse for Hungarian on this evidence, but large-v3 wins consistently. No official OpenAI hu numbers found.
- Fine-tuned Hungarian: 
  - benmajor27/whisper-large-v3-hu_full: best on Common Voice 17 (9.42 WER), but slightly worse than stock large-v3 on FLEURS (20.54 vs 19.77) [V sarpba table]. Likely overfit to CV; no CT2 version verified.
  - sarpba/whisper-hu-large-v3-turbo-finetuned: best normalized WER on KTH (13.95 vs 16.29), CT2 version not found [V sarpba table; https://huggingface.co/sarpba/whisper-hu-large-v3-turbo-finetuned].
  - BuzzASR/hungarian: large-v3 fine-tune, FLEURS 14.7, CV25 10.45, MIT, HF safetensors F16 (needs CT2 conversion; timestamps behaviour unverified) [V: https://huggingface.co/BuzzASR/hungarian].
  - Maxdorger29/whisper-large-v3-hungarian-lora: merged, exported to CT2, faster-whisper example in card; trained only on FLEURS hu (small) [V search snippet: https://huggingface.co/Maxdorger29/whisper-large-v3-hungarian-lora].
  - Hungarians/faster-large-v2-hu-cleaned-float32: CT2, large-v2 base, sparse card [V search snippet].
  - Caveat [I]: fine-tunes can lose timestamp/punctuation/other-language ability and may be tuned to read-speech corpora; benchmark on your own subtitle audio (movie/TV dialogue, music, noise) before adopting. Convert with `ct2-transformers-converter` (needs torch/transformers only at conversion time, extra `conversion`).
- distil-large-v3 / v3.5: English-only in practice; faster-whisper 1.2.0 added distil-large-v3.5 support [V: faster-whisper releases]. Not for Hungarian [I].
- large-v3 vs v2 hallucination: Deepgram (vendor) reported v3 hallucinates ~4x more than v2; user reports mixed; OpenAI claims 10-20% lower error [V search: https://deepgram.com/blog/whisper-v3-results]. Weak evidence either way; mitigate with VAD + thresholds (sec. 4).

## 3. Is "one batch per language" required? NO.
- [V] faster-whisper `transcribe(language=...)` takes language per call; default None = auto-detect per file. Recent commit raises an error if the language is unsupported by the loaded model (so a monolingual `.en` model would reject non-English; multilingual large-v3/turbo accept any of the 99) [V commit 9e472ae, 2026-10-16].
- [I, strongly expected from Whisper's design] Language is just a decoder prompt token (`<|hu|>`) chosen per call; the weights are language-agnostic, so one loaded WhisperModel/BatchedInferencePipeline can process hu, en, de files back to back with no reload. Test once with a mixed-language fixture to confirm.
- Caveat inside one batched call: BatchedInferencePipeline.transcribe takes one `language` per call (one audio per call) -> per-file language is natural. Mixed-language content inside one file needs `multilingual=True` (default False) which is less reliable [V param defaults; I on reliability].
 - WhisperX alignment (wav2vec2) DOES use a separate model per language (`load_align_model(language_code=...)`, e.g. ~1.2 GB xlsr-53 for hu); switching language means loading another aligner [V: whisperx/alignment.py DEFAULT_ALIGN_MODELS_*]. With alignment, process jobs grouped by language.
- Practical: requirement "one fixed target language per batch" is a product/UX simplification and is fine, but the worker should accept `language` per job/file and must NOT assume reload. Forcing language is recommended over auto-detect (avoids wrong-language detection on short/music-intro audio) [I].
- Note: "target language" = spoken language (transcription). Whisper's translate task goes only to English; not subtitle-translation.

## 4. Failure modes and mitigations
Verified parameter defaults [V: https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py]:
- WhisperModel.transcribe: beam 5, temperature fallback [0..1.0], compression_ratio_threshold 2.4, log_prob_threshold -1.0, no_speech_threshold 0.6, condition_on_previous_text True, hallucination_silence_threshold None, vad_filter False, word_timestamps False.
- BatchedInferencePipeline.transcribe: batch_size 8, vad_filter True, only temperature 0.0 used (no fallback), condition_on_previous_text forced False, hallucination_silence_threshold NOT available, without_timestamps True, VAD min_silence 160 ms and max_speech = chunk_length (30 s).
- Silero VAD defaults: threshold 0.5, min_silence 2000 ms, speech_pad 400 ms, min_speech 0, max_speech inf [V: vad.py].
Implications and recommended settings [I unless noted]:
- Hallucination in silence/music: always VAD (Silero v6 is in faster-whisper). Whisper has documented hallucination from non-speech audio [V: https://arxiv.org/html/2501.11378v1]. For sequential mode: vad_filter=True, vad_parameters(min_silence_duration_ms=500, speech_pad_ms=200-400, threshold 0.4-0.5), condition_on_previous_text=False, hallucination_silence_threshold=2.0 together with word_timestamps=True (it requires word timestamps), keep temperature fallback, compression_ratio_threshold 2.4, log_prob_threshold -1.0, no_speech_threshold 0.6. Post-filter segments with no_speech_prob high + avg_logprob low, and common phantom strings (e.g. "Thanks for watching", subtitle credits) per language.
- Batched mode trade-off: faster (several x on GPU; faster-whisper 1.1.0 claims 4x faster batched [V releases]) but no temperature fallback and no hallucination_silence_threshold, i.e. fewer guards. Option: batched by default, and re-run a file in sequential mode if post-checks flag it (repetition ratio, compression ratio of full text, segment count 0 on non-silent audio).
- Repetition loops: condition_on_previous_text=False; compression_ratio check; dedupe consecutive identical segments in post-process.
- Music-only audio: VAD yields zero speech -> empty segments. Treat empty output as a valid result: write an empty/placeholder SRT (or a status "no_speech") rather than failing. [I]
- Long files (multi-hour): faster-whisper streams segments lazily (generator) and processes 30 s windows, so GPU memory is roughly constant; host RAM holds the decoded float32 16 kHz mono waveform (~230 MB per hour) [I: 16000*4*3600=230 MB]. For 10 h files ~2.3 GB, fine. Pre-decode with ffmpeg to 16 kHz mono wav in chunks only if memory is a concern; batched mode (VAD chunks x batch_size) raises GPU use: large-v3 fp16 ~ 5-8 GB at batch 16 [I; WhisperX README claims <8 GB for large-v2 beam 5: V https://github.com/m-bain/whisperX]. A5000 24 GB has ample headroom.
- Corrupted inputs / unsupported codecs: faster-whisper decodes via PyAV with bundled FFmpeg (no system ffmpeg needed) [V README]. PyAV wheels bundle FFmpeg but docs do not list included codecs/version [V: https://pyav.basswood.io/docs/stable/overview/installation.html]. Wheels require Python >=3.12 for latest av 19.0.2 [V PyPI json]; faster-whisper only needs av>=11, so older av works on older Python. AAC, MP3, Opus/Vorbis, FLAC, PCM are certain in any FFmpeg build [I]; AC3/E-AC3 are native FFmpeg decoders and DTS (core) is native `dca` [I] but this was NOT verified for the PyAV wheel build. Mitigation (recommended, robust): do NOT rely on PyAV for odd codecs; run system ffmpeg (`-vn -ac 1 -ar 16000 -f wav/pcm_s16le`, or `-map 0:a:N`) to a temp wav and pass a numpy array/path to faster-whisper. This also handles multi-track mka, DTS-HD, TrueHD, and pins the audio stream choice. Add a startup self-test decoding sample files of each codec.
- Timeouts per file: run each file in a subprocess or thread with a deadline (e.g. max(10 min, audio_duration * 0.5)); faster-whisper has no internal timeout [I]. On CUDA OOM or hang: kill, mark failed, continue batch. Catch exceptions per file; never let one file abort the batch.
- Wrong-language/forced-language mismatch: if forced `language=hu` on an English file, Whisper tends to translate/garble; consider a cheap language-detect sanity check (language_detection_segments) and warn if probability for the forced language is very low [I].

## 5. Subtitle formatting
- Segments from Whisper are variable length (often up to ~30 s window text); subtitles need splitting. Use word_timestamps=True and build cues from words [I]:
  - max ~42 characters per line, max 2 lines (Netflix-style guideline [I; not fetched]), cue duration 1-7 s (min ~0.83-1 s), split at sentence end/punctuation, long pauses (>0.5-0.8 s), then at max chars; ~17 chars/s reading speed cap; avoid orphan words.
  - Hungarian words are long; consider max 42 chars still but prefer splitting at commas/conjunctions.
  - Guard against word timestamp artifacts: clamp end >= start, enforce non-overlap, minimum gap 1-2 frames.
- Libraries:
  - pysubs2 1.9.1 uploaded 2026-10-30, MIT, Python >=3.12 [V: https://pypi.org/pypi/pysubs2/json]. Active. Supports SRT/VTT/ASS; good choice if Python 3.12+.
  - srt 3.5.3, last upload 2023-03-28, MIT, Python >=2.7 [V: https://pypi.org/pypi/srt/json]. Stable/feature-complete, unmaintained but tiny; fine for SRT only.
  - Writing SRT/VTT by hand is ~30 lines (HH:MM:SS,mmm vs HH:MM:SS.mmm + "WEBVTT" header) and avoids dependency [I].
  - openai-whisper writers (whisper.utils) need torch import; faster-whisper has none. WhisperX has write utilities but pulls torch.

## 6. CUDA / cuDNN
- faster-whisper README: GPU needs cuBLAS and cuDNN 9 for CUDA 12; latest ctranslate2 supports only CUDA 12 + cuDNN 9; pin ctranslate2==4.4.0 for CUDA 12/cuDNN 8, 3.24.0 for CUDA 11 [V: https://github.com/SYSTRAN/faster-whisper].
- ctranslate2 4.9.1 PyPI metadata lists "NVIDIA CUDA 12.4" [V: https://pypi.org/pypi/ctranslate2/json]. CUDA 13 support: feature request #1933 (opened 2025-11-17) still open; PR #2160 "Support CUDA 13 source builds" open, updated 2026-11-19 [V: https://github.com/OpenNMT/CTranslate2/issues?q=CUDA+13]. => No official CUDA 13 wheels as of today. Don't plan on CUDA 13 userland.
- Ampere sm_86: covered by the CUDA 12 wheels [I; sm_86 is a long-standing CT2 target; 4.9.1 additionally adds Blackwell].
- CUDA 12.8/12.9 base images: fine, since CUDA 12.x runtime libs are compatible within the major version [I]; the baseline's cu128 image proves cu12.8 works with ctranslate2 4.8.4 (see reference-sttcli.md). A newer host driver (including CUDA 13-era drivers) runs CUDA 12 apps through driver backward compatibility [I; not verified in sources]. Verify on the actual Runpod host: the machine's driver must support >= CUDA 12.4 (>= ~550) [I]; Runpod lets you filter pods by CUDA version.
- Wheels alternative [V: README + search]: `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` and set LD_LIBRARY_PATH to the site-packages `nvidia/cublas/lib` and `nvidia/cudnn/lib` dirs BEFORE starting Python (README gives the one-liner using `os.path.dirname(nvidia.cublas.lib.__file__)`). A cuDNN-9 runtime base image (e.g. nvidia/cuda:12.x-cudnn-runtime) is the simpler alternative and avoids LD_LIBRARY_PATH fiddling [I]. If you no longer ship torch you must supply cuBLAS/cuDNN yourself (torch wheels used to bring them in the baseline image) [I]. Always assert on startup that `ctranslate2.get_cuda_device_count() > 0` so a missing lib doesn't silently fall back to CPU (note: missing libs usually raise "Could not load library libcudnn_ops..." [V search: runaihome blog]).
- WhisperX requires CUDA toolkit 12.8 per its README [V]; your baseline fits that.
- Python: PyAV 19 needs >=3.12; ctranslate2 4.9.1 supports 3.9-3.15 [V]. Use Python 3.12.

## 7. Model weight caching
Sizes [V]: large-v3 CT2 fp16 3.09 GB; large-v3-turbo CT2 1.62 GB; plus tiny tokenizer/vocab files. Silero VAD ONNX ships inside the faster-whisper package assets [V: vad.py loads silero_vad_v6.onnx from package assets]. (WhisperX would additionally download wav2vec2 aligners per language, ~1.2 GB for xlsr-53, and pyannote models [I].)
Options:
1. Bake into image (`huggingface_hub.snapshot_download` or `WhisperModel("large-v3")` at build, HF_HOME set to a path in the image): zero cold-start download, deterministic, but image grows 3-5 GB and pushes/pulls slower; Runpod serverless FlashBoot/caching likes baked models [I]. Best for the default model (large-v3).
2. Runtime download to Runpod network volume (HF_HOME=/runpod-volume/hf): small image, shared across workers, one-time download; first-run latency and dependency on HF availability/network volume region [I]. Good for optional models (turbo, fine-tunes).
3. Hybrid (recommended): bake large-v3, mount volume as HF_HOME fallback for optional models; set HF_HUB_OFFLINE=1 in production once cached, and pass a local path to WhisperModel to avoid network calls [I].
- Fine-tuned HF safetensors need one-time CT2 conversion (`ct2-transformers-converter --model X --output_dir Y --quantization float16 --copy_files tokenizer.json preprocessor_config.json`) done at build time [V README mentions conversion; exact flags I].

## Comparison against the STTCLI baseline (WhisperX 3.7.9, large-v3, fp16, bs 16)
- Same decoder (faster-whisper 1.2.1 / ct2 4.8.4) so transcription accuracy of large-v3 fp16 is equivalent. Differences come from: (a) WhisperX uses pyannote VAD and wav2vec2 alignment (word times often tighter, esp. with Hungarian aligner present); (b) faster-whisper directly uses Silero VAD v6, which is lighter; (c) WhisperX batched mode has no timestamp tokens and no temperature fallback (same for faster-whisper batched).
- Gains of switching to direct faster-whisper: no torch (image from several GB to ~1 GB + CUDA libs), fewer version-pin conflicts (WhisperX has needed pins like torch/torchvision/torchcodec for torch 2.8 [V release 3.8.5 notes]), no HF token / pyannote dependency, per-file language switch with no extra aligner model reload (WhisperX needs a separate align model per language: reload when language changes [I]).
- Losses: forced alignment word timing precision, diarization (not needed). If subtitle sync from Whisper word timestamps proves insufficient on your test set, add WhisperX alignment as an optional stage only.
- Keep: large-v3, float16, batch 16 (A5000 has headroom; batch 8 is the faster-whisper default), SRT.
- Version caution: baseline WhisperX 3.7.9 is older than 3.8.6; if you keep WhisperX, upgrade and re-test.

## Open items / unverified
- PyAV wheel codec coverage (ac3/eac3/dts/truehd) - test or use system ffmpeg.
- Mixed-language sequential run - confirm with a test.
- Real A5000 throughput numbers for large-v3 fp16 batched (not found in sources; blog figures conflicting).
- CT2 conversions of the Hungarian fine-tunes' quality on subtitle-style audio.
- Whether a CUDA 13 host driver on the chosen Runpod machine runs ct2 CUDA 12 wheels (expected yes).
