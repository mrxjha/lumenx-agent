#!/bin/sh
# Single image, three roles. The Railway service picks its role via SERVICE_ROLE:
#   web       -> FastAPI wiki explorer (public, binds $PORT)
#   dashboard -> Streamlit human-review + cost UI (public, binds $PORT)
#   poller    -> background auto-reply loop (no public port)
#
# All three share the same Postgres DB via DATABASE_URL, so the poller's drafts
# show up in the dashboard's review queue.
set -e

ROLE="${SERVICE_ROLE:-web}"
echo "[entrypoint] starting role=$ROLE"

# Ensure the schema exists (idempotent: CREATE TABLE IF NOT EXISTS). Tolerate
# transient DB errors so the container can still boot and retry on first query.
python -m db.connection || echo "[entrypoint] db init skipped (will rely on existing schema)"

case "$ROLE" in
  web)
    exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  dashboard)
    exec streamlit run dashboard/app.py \
      --server.headless true \
      --server.address 0.0.0.0 \
      --server.port "${PORT:-8000}" \
      --browser.gatherUsageStats false
    ;;
  poller)
    exec python -m agent.poller
    ;;
  *)
    echo "[entrypoint] ERROR: unknown SERVICE_ROLE='$ROLE' (expected web|dashboard|poller)" >&2
    exit 1
    ;;
esac
