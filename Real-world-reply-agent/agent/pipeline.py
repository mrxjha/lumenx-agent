"""Pipeline orchestrator — thread input -> intent -> context -> draft -> score -> route.

Flow:
  1. Mirror thread + open a pending drafts row (gives us a draft_id to link token_usage to)
  2. Intent classification (Haiku)
  3. Context build (wiki + thread + past convs + feedback examples)
  4. Reply drafting (Sonnet)
  5. Confidence scoring (MLP) — routes to auto_sent or pending_review
  6. Persist draft_text, context_window, confidence, status

Auto-send only sets status='auto_sent' in the local DB; actually posting to
LumenX is the Phase 5 poller's job. This keeps the pipeline a pure function
of its inputs and reusable from smoke tests / dashboard.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

from agent.context_builder import ThreadInput, build_context, BuiltContext
from agent.intent import IntentResult, classify
from agent.llm_draft import DraftResult, draft_reply
from confidence.predict import ConfidenceScore, predict as predict_confidence
from config import settings
from db.connection import get_connection

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    thread_id: str
    draft_id: int
    intent: IntentResult
    draft: DraftResult
    context: BuiltContext
    confidence: ConfidenceScore
    status: str                  # 'auto_sent' | 'pending_review'
    total_cost_usd: float


def _insert_pending_draft(thread_id: str, intent: Optional[str] = None) -> int:
    conn = get_connection()
    try:
        # RETURNING id is portable across SQLite (>=3.35) and Postgres, and avoids
        # the non-portable sqlite-only cursor.lastrowid.
        cur = conn.execute(
            """INSERT INTO drafts (thread_id, intent, draft_text, status)
               VALUES (?, ?, '', 'pending_review')
               RETURNING id""",
            (thread_id, intent),
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id
    finally:
        conn.close()


def _set_draft_intent(draft_id: int, intent: str) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE drafts SET intent = ? WHERE id = ?", (intent, draft_id))
        conn.commit()
    finally:
        conn.close()


def _finalize_draft(
    draft_id: int,
    draft_text: str,
    ctx: BuiltContext,
    confidence: ConfidenceScore,
    status: str,
) -> None:
    """Write the drafted text + serialized context window back to the row,
    plus the confidence score and the routing decision."""
    ctx_dump = {
        "intent": ctx.intent,
        "latest_message": ctx.latest_message,
        "sources_used": ctx.sources_used,
        "wiki_chars": len(ctx.wiki_context),
        "thread_history": ctx.thread_history,
        "past_conv_summary": ctx.past_conv_summary,
        "feedback_examples": ctx.feedback_examples,
        "confidence": {
            "score": confidence.score,
            "threshold": confidence.threshold,
            "decision": confidence.decision,
            "reason": confidence.reason,
        },
    }
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE drafts
               SET draft_text = ?, context_window = ?, confidence = ?, status = ?
               WHERE id = ?""",
            (draft_text, json.dumps(ctx_dump), confidence.score, status, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def _upsert_thread(thread: ThreadInput, intent: Optional[str]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO threads (id, username, display_name, product_id, intent)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   username     = excluded.username,
                   display_name = excluded.display_name,
                   product_id   = excluded.product_id,
                   intent       = excluded.intent,
                   last_synced_at = CURRENT_TIMESTAMP""",
            (
                thread.thread_id,
                thread.customer_username,
                thread.customer_display,
                thread.product_id,
                intent,
            ),
        )
        # Also mirror any messages we have not seen yet
        for m in thread.messages:
            try:
                conn.execute(
                    """INSERT INTO messages
                       (thread_id, remote_msg_id, role, text)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (thread_id, remote_msg_id) DO NOTHING""",
                    (
                        thread.thread_id,
                        m.get("id") or m.get("remote_msg_id"),
                        m.get("role", "customer"),
                        m.get("text", ""),
                    ),
                )
            except Exception:
                # Bad message rows shouldn't kill the pipeline
                pass
        conn.commit()
    finally:
        conn.close()


def run(thread: ThreadInput) -> PipelineResult:
    # 1. Open a pending draft row up-front so every LLM call (intent + draft) can
    #    be linked to it in token_usage.
    _upsert_thread(thread, intent=None)
    draft_id = _insert_pending_draft(thread.thread_id, intent=None)

    # 2. Intent classification (logged against this draft_id)
    latest = _latest_customer_text(thread.messages)
    prior = thread.messages[:-1] if thread.messages else []
    intent = classify(latest, prior, draft_id=draft_id)
    log.info("intent=%s confidence=%.2f reason=%s", intent.intent, intent.confidence, intent.reason)
    _set_draft_intent(draft_id, intent.intent)

    # 3. Context
    ctx = build_context(thread, intent.intent)

    # 4. Draft (token_usage gets linked to draft_id)
    draft = draft_reply(ctx, draft_id=draft_id)

    # 5. Confidence score + routing decision
    confidence = predict_confidence(
        draft=draft.text,
        intent=intent.intent,
        retrieval_hits=len(ctx.sources_used),
    )
    # Honor the global safety switch: even a confident draft only auto-sends when
    # AUTO_SEND_ENABLED is on. Otherwise everything routes to human review.
    auto = confidence.decision == "auto_send" and settings.auto_send_enabled
    status = "auto_sent" if auto else "pending_review"
    log.info(
        "confidence score=%s decision=%s -> status=%s",
        f"{confidence.score:.3f}" if confidence.score is not None else "n/a",
        confidence.decision, status,
    )

    # 6. Persist
    _finalize_draft(draft_id, draft.text, ctx, confidence, status)

    return PipelineResult(
        thread_id=thread.thread_id,
        draft_id=draft_id,
        intent=intent,
        draft=draft,
        context=ctx,
        confidence=confidence,
        status=status,
        total_cost_usd=intent.cost_usd + draft.cost_usd,
    )


def _latest_customer_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "customer" and (m.get("text") or "").strip():
            return m["text"].strip()
    return (messages[-1].get("text") or "").strip() if messages else ""
