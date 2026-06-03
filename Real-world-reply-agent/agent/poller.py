"""Telegram auto-reply loop.

Loop:
  1. getUpdates(offset) long-poll for new messages.
  2. For each incoming text message:
       a. Build a ThreadInput from this chat's stored history + the new message.
       b. Run the pipeline (intent -> context -> draft -> confidence -> route).
       c. If pipeline.status == 'auto_sent' (only when AUTO_SEND_ENABLED) and not
          --dry-run: sendMessage the draft back to the chat, record it, mark sent.
       d. Else: leave the draft as pending_review for the dashboard.
  3. Persist the update offset so restarts don't re-process old messages.

Run with:   python -m agent.poller            # production loop
            python -m agent.poller --once     # one getUpdates pass then exit
            python -m agent.poller --dry-run  # never send replies back to Telegram

NOTE: only ONE poller may run per bot at a time (Telegram rejects concurrent
getUpdates with HTTP 409 Conflict), and it must not coexist with a set webhook.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Optional

# httpx logs full URLs at INFO level which would expose the bot token in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

from agent.context_builder import ThreadInput
from agent.pipeline import PipelineResult, run as run_pipeline
from config import PROJECT_ROOT, settings
from db.connection import get_connection, init_db
from tg.client import TelegramClient

log = logging.getLogger(__name__)

OFFSET_FILE = PROJECT_ROOT / "data" / "tg_offset.json"


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------

def _load_offset() -> Optional[int]:
    if not OFFSET_FILE.exists():
        return None
    try:
        return json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset")
    except Exception as e:
        log.warning("Could not read offset file (%s) — starting fresh", e)
        return None


def _save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    """Prior messages for this chat, oldest first, shaped for ThreadInput."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT remote_msg_id, role, text, created_at
               FROM messages WHERE thread_id = ? ORDER BY id ASC""",
            (thread_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r["remote_msg_id"], "role": r["role"], "text": r["text"], "created_at": r["created_at"]}
        for r in rows
    ]


def _record_agent_message(thread_id: str, remote_msg_id: str, text: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO messages (thread_id, remote_msg_id, role, text)
               VALUES (?, ?, 'agent', ?)
               ON CONFLICT (thread_id, remote_msg_id) DO NOTHING""",
            (thread_id, remote_msg_id, text),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_draft_sent(draft_id: int, final_text: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE drafts SET final_text = ?, sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (final_text, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Update -> ThreadInput
# ---------------------------------------------------------------------------

def _to_thread_input(msg: dict[str, Any]) -> ThreadInput:
    chat_id = str(msg["chat"]["id"])
    frm = msg.get("from", {}) or {}
    username = frm.get("username") or str(frm.get("id") or chat_id)
    display = frm.get("first_name") or username

    history = _load_thread_messages(chat_id)
    history.append({
        "id": str(msg["message_id"]),
        "role": "customer",
        "text": msg.get("text", ""),
    })
    return ThreadInput(
        thread_id=chat_id,
        customer_username=username,
        customer_display=display,
        product_id=None,
        messages=history,
    )


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

class Poller:
    def __init__(
        self,
        *,
        client: Optional[TelegramClient] = None,
        interval_sec: Optional[int] = None,
        dry_run: bool = False,
    ) -> None:
        self.client = client or TelegramClient()
        self.long_poll = interval_sec if interval_sec is not None else settings.poll_interval_sec
        self.dry_run = dry_run
        self.offset = _load_offset()
        self._stop = False

    def request_stop(self, *_):
        log.info("Stop requested — exiting after the current poll.")
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        me = self.client.get_me()
        log.info("Poller started — bot=@%s long_poll=%ss dry_run=%s auto_send=%s",
                 me.get("username"), self.long_poll, self.dry_run, settings.auto_send_enabled)
        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                log.exception("Poll tick failed: %s", e)
                time.sleep(3)  # back off on transient errors (e.g. network)
        log.info("Poller stopped.")

    def tick(self) -> list[PipelineResult]:
        """One getUpdates pass. Returns the pipeline results produced."""
        updates = self.client.get_updates(offset=self.offset, timeout=self.long_poll)
        results: list[PipelineResult] = []
        for upd in updates:
            self.offset = upd["update_id"] + 1  # ack this update regardless of outcome
            msg = upd.get("message")
            if not msg or "text" not in msg:
                continue  # ignore non-text updates (stickers, joins, edits, ...)
            try:
                results.append(self._handle_message(msg))
            except Exception as e:
                log.exception("Failed handling message in chat %s: %s",
                              msg.get("chat", {}).get("id"), e)
        if self.offset is not None:
            _save_offset(self.offset)
        return results

    def _handle_message(self, msg: dict[str, Any]) -> PipelineResult:
        chat_id = msg["chat"]["id"]
        if not self.dry_run:
            try:
                self.client.send_typing(chat_id)
            except Exception:
                pass  # cosmetic only

        thread = _to_thread_input(msg)
        result = run_pipeline(thread)
        log.info("chat=%s intent=%s conf=%s -> %s", chat_id, result.intent.intent,
                 f"{result.confidence.score:.3f}" if result.confidence.score is not None else "n/a",
                 result.status)

        if result.status == "auto_sent" and not self.dry_run:
            try:
                sent = self.client.send_message(chat_id, result.draft.text)
                _record_agent_message(thread.thread_id, str(sent.get("message_id", "")), result.draft.text)
                _mark_draft_sent(result.draft_id, result.draft.text)
                log.info("auto-sent draft=%s chat=%s", result.draft_id, chat_id)
            except Exception as e:
                log.exception("Failed to send reply to chat %s: %s — demoting to review", chat_id, e)
                _demote_to_pending(result.draft_id)
        elif result.status == "auto_sent" and self.dry_run:
            log.info("[dry-run] would auto-send draft=%s chat=%s", result.draft_id, chat_id)

        return result


def _demote_to_pending(draft_id: int) -> None:
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
    parser.add_argument("--once", action="store_true", help="Run a single getUpdates pass and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Never send replies back to Telegram.")
    parser.add_argument("--interval", type=int, default=None, help="Override long-poll timeout (s).")
    args = parser.parse_args()

    init_db()
    poller = Poller(interval_sec=args.interval, dry_run=args.dry_run)
    if args.once:
        results = poller.tick()
        print(f"[ok] processed {len(results)} message(s) in single pass.")
        return 0
    poller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
