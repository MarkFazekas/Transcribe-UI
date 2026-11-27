# syntax=docker/dockerfile:1.7
# Runpod worker image (architecture §5.1). Build from the repository root:
#   docker build -f apps/worker/Dockerfile -t transcribe-ui-worker .

FROM ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 AS uv

FROM nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

# Python and the locked environment live outside the source tree; models in fixed caches
# that the worker reads at run time (the same paths as at build time).
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/models/huggingface \
    TORCH_HOME=/opt/models/torch \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (cached until the lock changes), then the workspace code.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package transcribe-worker --extra engine --no-install-workspace

COPY packages/contracts packages/contracts
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package transcribe-worker --extra engine --no-editable

# cuBLAS and cuDNN come from the torch wheels; CTranslate2 finds them through this path.
ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.13/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.13/site-packages/nvidia/cudnn/lib

RUN python -m transcribe_worker.engine.bake

# tini reaps zombies and forwards signals; the entry never exits while the pod exists.
ENTRYPOINT ["tini", "--", "transcribe-worker-entry"]
