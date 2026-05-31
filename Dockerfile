FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: curl for healthchecks; build-essential only for wheels that
# need to compile (Levenshtein has prebuilt wheels for cpython 3.12 so usually
# not needed, but kept for forward compatibility).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Build the wiki at image-build time so the container is ready to serve
# immediately. Falls back gracefully at runtime if the LumenX API is unreachable.
RUN python -m wiki.builder || echo "[warn] wiki build skipped — will retry at runtime"

# Initialize the SQLite schema (idempotent — safe to re-run on every boot)
RUN python -m db.connection

ENV PORT=8000
EXPOSE 8000

# Default entrypoint runs the wiki + healthcheck server. Override via railway.toml
# `[services.<name>] start = "..."` for the poller and dashboard services.
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
