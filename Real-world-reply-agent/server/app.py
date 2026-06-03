"""Minimal web service for the `web` Railway role.

Not essential to the agent (the poller + dashboard do the real work), but it gives
a public health endpoint and a small landing page so the `web` service — which is
`entrypoint.sh`'s default role — boots cleanly and passes Railway health checks.
It deliberately serves NO secrets and NO file listing.

Run with:  uvicorn server.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from config import settings

app = FastAPI(title="Ramco Auto-Reply Agent")


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return (
        "<html><body style='font-family:sans-serif;max-width:640px;margin:40px auto'>"
        "<h2>Ramco Auto-Reply Agent</h2>"
        "<p>This is the lightweight web/health service. The two working services are:</p>"
        "<ul>"
        "<li><b>poller</b> — the Telegram bot auto-reply loop</li>"
        "<li><b>dashboard</b> — the human-review + cost dashboard (separate Railway service)</li>"
        "</ul>"
        f"<p>Auto-send: <b>{'ON' if settings.auto_send_enabled else 'OFF (review all)'}</b> · "
        f"confidence threshold: <b>{settings.confidence_threshold:.2f}</b></p>"
        "</body></html>"
    )


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
