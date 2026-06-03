"""Thin data-access helpers for the Streamlit dashboard.

Each helper opens its own connection and returns plain dicts / pandas DataFrames
so the Streamlit page code stays declarative.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd

from db.connection import get_connection


# ---------- review queue ----------

def fetch_pending_drafts(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT d.id, d.thread_id, d.intent, d.draft_text, d.confidence,
                      d.status, d.context_window, d.created_at,
                      t.username, t.display_name, t.product_id
               FROM drafts d
               LEFT JOIN threads t ON t.id = d.thread_id
               WHERE d.status = 'pending_review'
               ORDER BY d.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def fetch_draft(draft_id: int) -> Optional[dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT d.*, t.username, t.display_name, t.product_id
               FROM drafts d
               LEFT JOIN threads t ON t.id = d.thread_id
               WHERE d.id = ?""",
            (draft_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT role, text, created_at FROM messages
               WHERE thread_id = ? ORDER BY id ASC""",
            (thread_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def approve_draft(
    draft_id: int,
    final_text: str,
    thumbs: Optional[int],
    correction: Optional[str],
    send_to_telegram: bool = True,
) -> tuple[bool, Optional[str]]:
    """Approve a draft. Updates local DB, optionally sends the reply via Telegram.

    Returns (sent_remote, error_message). `sent_remote` is False if we skipped
    or failed to send; `error_message` describes the failure. (Sending will fail
    on a network that blocks Telegram — that's expected; the draft is still saved.)
    """
    conn = get_connection()
    try:
        thread_id_row = conn.execute(
            "SELECT thread_id, confidence FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        thread_id = thread_id_row["thread_id"] if thread_id_row else None
        confidence = thread_id_row["confidence"] if thread_id_row else None

        conn.execute(
            """UPDATE drafts
               SET final_text = ?, status = 'human_sent', sent_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (final_text, draft_id),
        )
        if thumbs is not None or correction:
            conn.execute(
                "INSERT INTO feedback (draft_id, thumbs, correction) VALUES (?, ?, ?)",
                (draft_id, thumbs, correction or None),
            )
        conn.commit()
    finally:
        conn.close()

    if not send_to_telegram or not thread_id:
        return False, None

    try:
        from tg.client import TelegramClient
        sent = TelegramClient().send_message(thread_id, final_text)
        # Mirror the agent's sent message into the local thread for continuity.
        try:
            conn = get_connection()
            conn.execute(
                """INSERT INTO messages (thread_id, remote_msg_id, role, text)
                   VALUES (?, ?, 'agent', ?)
                   ON CONFLICT (thread_id, remote_msg_id) DO NOTHING""",
                (thread_id, str(sent.get("message_id", "")), final_text),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def reject_draft(draft_id: int, correction: Optional[str]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE drafts SET status = 'rejected' WHERE id = ?",
            (draft_id,),
        )
        conn.execute(
            "INSERT INTO feedback (draft_id, thumbs, correction) VALUES (?, -1, ?)",
            (draft_id, correction or None),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- cost dashboard ----------

def fetch_token_usage_df() -> pd.DataFrame:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, draft_id, model, step, input_tokens, output_tokens,
                      cost_usd, created_at
               FROM token_usage
               ORDER BY created_at DESC""",
        ).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"])
    return df


def fetch_recent_replies_df(limit: int = 50) -> pd.DataFrame:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""SELECT d.id AS draft_id, d.thread_id, d.intent, d.status, d.confidence,
                       d.created_at, d.sent_at,
                       (SELECT SUM(cost_usd) FROM token_usage WHERE draft_id = d.id) AS cost_usd,
                       (SELECT SUM(input_tokens) FROM token_usage WHERE draft_id = d.id) AS input_tokens,
                       (SELECT SUM(output_tokens) FROM token_usage WHERE draft_id = d.id) AS output_tokens
                FROM drafts d
                ORDER BY d.created_at DESC
                LIMIT {int(limit)}""",
        ).fetchall()
    finally:
        conn.close()
    return pd.DataFrame([dict(r) for r in rows])


# ---------- shared ----------

def parse_context_window(ctx_json: Optional[str]) -> dict[str, Any]:
    if not ctx_json:
        return {}
    try:
        return json.loads(ctx_json)
    except Exception:
        return {}


def fetch_summary_counts() -> dict[str, int]:
    conn = get_connection()
    try:
        out: dict[str, int] = {}
        for status in ("pending_review", "auto_sent", "human_sent", "rejected"):
            cur = conn.execute(
                "SELECT COUNT(*) AS c FROM drafts WHERE status = ?", (status,)
            ).fetchone()
            out[status] = cur["c"] if cur else 0
        out["total_drafts"] = sum(out.values())
        return out
    finally:
        conn.close()
