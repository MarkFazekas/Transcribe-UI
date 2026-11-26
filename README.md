# Transcribe-UI

Batch subtitle generation for local video and audio libraries.

- **Desktop app** (Windows, macOS, Linux; GUI and JSON-driven CLI): browse a folder tree, filter
  and select media files (for example "has a Hungarian subtitle but no English one"), convert
  their audio losslessly and upload it to Cloudflare R2 in resumable batches, start the GPU
  worker, then download the subtitles next to the media or into a folder layout of your choice.
- **Worker** (Runpod, RTX A5000): a public Docker image that transcribes every ready batch with
  Whisper (`large-v3` via faster-whisper), uploads the subtitles, keeps a backup on a Runpod
  Global Volume and terminates its own pod.

> Status: design phase. The technical design is in
> [`docs/ai/architecture.md`](docs/ai/architecture.md); open questions are in
> [`docs/ai/questions-for-product.md`](docs/ai/questions-for-product.md) and
> [`docs/ai/questions-for-architect.md`](docs/ai/questions-for-architect.md).

## Development

Requirements: [uv](https://docs.astral.sh/uv/), git, ffmpeg (for tests).

```
uv sync
uv run ruff format . && uv run ruff check . && uv run flake8 .
```

Conventions (commits, branches, docs, quality bar) are in [`CLAUDE.md`](CLAUDE.md) and
[`docs/ai/tooling.md`](docs/ai/tooling.md).

## Licence

Apache-2.0, see [`LICENSE`](LICENSE).
