"""Inbox poller — drives the auto-reply loop.

Loop:
  1. GET /api/admin/inbox?since=<cursor>
  2. For each thread awaiting admin reply that we haven't already processed:
       a. GET /api/admin/threads/{id}
       b. Convert payload -> ThreadInput
       c. Run the pipeline (intent -> context -> draft -> confidence)
       d. If pipeline.status == 'auto_sent' AND --no-send is NOT set:
            POST /api/admin/threads/{id}/reply with draft text + confidence
       e. Else: leave as pending_review for the dashboard
  3. Sleep POLL_INTERVAL_SEC and repeat

State persisted to data/poller_state.json so restarts don't re-process.

Run with:   py -m agent.poller            # production loop
            py -m agent.poller --once     # single pass then exit
            py -m agent.poller --dry-run  # never POST replies back to LumenX
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agent.context_builder import ThreadInput
from agent.pipeline import PipelineResult, run as run_pipeline
from config import PROJECT_ROOT, settings
from db.connection import init_db
from lumenx.client import LumenXClient


log = logging.getLogger(__name__)

STATE_FILE = PROJECT_ROOT / "data" / "poller_state.json"


# ---------------------------------------------------------------------------
# Persistent cursor
# ---------------------------------------------------------------------------

@dataclass
class PollerState:
    last_server_time: Optional[str] = None
    thread_to_last_msg_id: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.thread_to_last_msg_id is None:
            self.thread_to_last_msg_id = {}

    @classmethod
    def load(cls) -> "PollerState":
        if not STATE_FILE.exists():
            return cls()
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return cls(
                last_server_time=data.get("last_server_time"),
                thread_to_last_msg_id=data.get("thread_to_last_msg_id") or {},
            )
        except Exception as e:
            log.warning("Could not read poller state (%s) — starting fresh", e)
            return cls()

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({
                "last_server_time": self.last_server_time,
                "thread_to_last_msg_id": self.thread_to_last_msg_id,
            }, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------

def _coerce_role(raw: Optional[str]) -> str:
    """LumenX uses 'admin' / 'customer'; we keep the same vocab in our DB."""
    r = (raw or "").lower().strip()
    if r in ("customer", "user", "client"):
        return "customer"
    if r in ("admin", "support", "agent"):
        return r if r in ("admin", "agent") else "admin"
    return r or "customer"


def _to_thread_input(thread_json: dict[str, Any]) -> ThreadInput:
    """Map the /api/admin/threads/{id} payload into a ThreadInput dataclass.

    LumenX shape (defensive — we tolerate small variations):
      {
        id, username, display_name, product_id,
        messages: [ { id, role, text, created_at }, ... ]
      }
    """
    msgs_raw = thread_json.get("messages") or thread_json.get("thread", {}).get("messages") or []
    messages: list[dict[str, Any]] = []
    for m in msgs_raw:
        if not isinstance(m, dict):
            continue
        messages.append({
            "id": m.get("id") or m.get("message_id") or m.get("remote_msg_id"),
            "role": _coerce_role(m.get("role")),
            "text": m.get("text") or m.get("content") or "",
            "created_at": m.get("created_at"),
        })

    return ThreadInput(
        thread_id=str(thread_json.get("id") or thread_json.get("thread_id")),
        customer_username=thread_json.get("username") or thread_json.get("user") or "unknown",
        customer_display=thread_json.get("display_name") or thread_json.get("username") or "Customer",
        product_id=thread_json.get("product_id"),
        messages=messages,
    )


def _latest_customer_msg_id(thread: ThreadInput) -> Optional[str]:
    for m in reversed(thread.messages):
        if m.get("role") == "customer":
            return str(m.get("id") or "") or None
    return None


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

class Poller:
    def __init__(
        self,
        *,
        client: Optional[LumenXClient] = None,
        interval_sec: Optional[int] = None,
        dry_run: bool = False,
    ) -> None:
        self.client = client or LumenXClient()
        self.interval = interval_sec if interval_sec is not None else settings.poll_interval_sec
        self.dry_run = dry_run
        self.state = PollerState.load()
        self._stop = False

    # graceful shutdown
    def request_stop(self, *_):
        log.info("Stop requested — finishing current pass and exiting.")
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        log.info(
            "Poller started — interval=%ss dry_run=%s base=%s",
            self.interval, self.dry_run, self.client.base_url,
        )
        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                log.exception("Poll tick failed: %s", e)
            for _ in range(self.interval):
                if self._stop:
                    break
                time.sleep(1)
        log.info("Poller stopped.")

    def tick(self) -> list[PipelineResult]:
        """One inbox sweep. Returns the pipeline results produced this tick."""
        inbox = self.client.get_inbox(since=self.state.last_server_time)
        server_time = inbox.get("server_time")
        entries = inbox.get("entries") or []
        log.info("inbox: server_time=%s entries=%d", server_time, len(entries))

        results: list[PipelineResult] = []
        for entry in entries:
            try:
                if not entry.get("awaiting_admin", True):
                    continue
                thread_meta = entry.get("thread") or entry
                thread_id = thread_meta.get("id") or thread_meta.get("thread_id")
                if not thread_id:
                    continue

                thread_json = self.client.get_thread(thread_id)
                thread = _to_thread_input(thread_json)
                if not thread.messages:
                    continue

                latest_msg_id = _latest_customer_msg_id(thread)
                prev_seen = self.state.thread_to_last_msg_id.get(thread.thread_id)
                if latest_msg_id and prev_seen == latest_msg_id:
                    # We already processed this exact customer message.
                    continue

                result = run_pipeline(thread)
                results.append(result)

                if result.status == "auto_sent" and not self.dry_run:
                    try:
                        self.client.post_reply(
                            thread_id=thread.thread_id,
                            text=result.draft.text,
                            draft_source="agent",
                            confidence=result.confidence.score,
                        )
                        log.info("auto-sent draft=%s thread=%s", result.draft_id, thread.thread_id)
                    except Exception as e:
                        log.exception("Failed to POST reply for thread %s: %s", thread.thread_id, e)
                        # Demote to pending_review so a human can finish the job
                        self._demote_to_pending(result.draft_id)
                elif result.status == "auto_sent" and self.dry_run:
                    log.info("[dry-run] would auto-send draft=%s thread=%s",
                             result.draft_id, thread.thread_id)

                if latest_msg_id:
                    self.state.thread_to_last_msg_id[thread.thread_id] = latest_msg_id

            except Exception as e:
                log.exception("Failed processing inbox entry: %s", e)
                continue

        if server_time:
            self.state.last_server_time = server_time
        self.state.save()
        return results

    @staticmethod
    def _demote_to_pending(draft_id: int) -> None:
        from db.connection import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE drafts SET status = 'pending_review' WHERE id = ? AND status = 'auto_sent'",
                (draft_id,),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single inbox sweep and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Never POST replies back to LumenX.")
    parser.add_argument("--interval", type=int, default=None, help="Override POLL_INTERVAL_SEC.")
    args = parser.parse_args()

    init_db()
    poller = Poller(interval_sec=args.interval, dry_run=args.dry_run)
    if args.once:
        results = poller.tick()
        print(f"[ok] processed {len(results)} thread(s) in single tick.")
        return 0
    poller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
