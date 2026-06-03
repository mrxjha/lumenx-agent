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
# immediately. Falls back to the committed wiki/products/*.md if the LumenX API
# (and its token) aren't available at build time.
RUN python -m wiki.builder || echo "[warn] wiki build skipped — using committed wiki pages"

# NOTE: the DB schema is initialized at RUNTIME by entrypoint.sh, not here —
# the Postgres database isn't reachable during the image build.

ENV PORT=8000
EXPOSE 8000

# One image, three roles. The running container picks its role from SERVICE_ROLE
# (web | dashboard | poller); see entrypoint.sh. No per-service start command is
# needed — services differ only by the SERVICE_ROLE env var.
CMD ["sh", "/app/entrypoint.sh"]
