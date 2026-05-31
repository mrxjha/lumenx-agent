"""Context Builder — assembles all knowledge sources into one structured prompt
that gets handed to the Sonnet drafter.

Sources (in order of priority):
  1. wiki_context        — selected wiki pages (policy + top-k + cross-ref expansion)
  2. thread_history      — full message log of the current thread
  3. past_conv_summary   — short summary of this user's prior threads
  4. feedback_examples   — past good Q->A pairs (from `feedback` + `drafts`)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from db.connection import get_connection
from prompts import CONTEXT_TEMPLATE
from wiki.loader import assemble_wiki_context


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class ThreadInput:
    """The minimum we need about a thread to draft a reply.

    Shapes are intentionally LumenX-agnostic — the caller (Phase 5 poller)
    is responsible for converting `/api/admin/threads/{id}` payloads into this.
    """
    thread_id: str
    customer_username: str
    customer_display: str
    product_id: Optional[str]
    messages: list[dict]              # [{role, text, created_at}, ...] oldest first


@dataclass
class BuiltContext:
    intent: str
    latest_message: str
    wiki_context: str
    thread_history: str
    past_conv_summary: str
    feedback_examples: str
    prompt: str                       # final filled-in user-turn prompt
    sources_used: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _format_thread_history(messages: Iterable[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        text = (m.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{role}] {text}")
    return "\n".join(lines) if lines else "(no prior messages in this thread)"


def _latest_customer_message(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "customer" and (m.get("text") or "").strip():
            return m["text"].strip()
    # Fallback: last message of any kind
    return (messages[-1].get("text") or "").strip() if messages else ""


def _past_conv_summary(username: str, current_thread_id: str, max_threads: int = 3) -> str:
    """Pull a few short snippets from this user's previous threads."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT t.id, t.product_id, t.intent
               FROM threads t
               WHERE t.username = ? AND t.id != ?
               ORDER BY t.created_at DESC
               LIMIT ?""",
            (username, current_thread_id, max_threads),
        ).fetchall()
        if not rows:
            return "(no prior conversations on record)"
        parts: list[str] = []
        for r in rows:
            first = conn.execute(
                """SELECT text FROM messages
                   WHERE thread_id = ? AND role = 'customer'
                   ORDER BY id ASC LIMIT 1""",
                (r["id"],),
            ).fetchone()
            opener = (first["text"][:140] + "...") if first else "(no opener)"
            parts.append(f"- thread {r['id']} on {r['product_id'] or 'n/a'} (intent={r['intent'] or '?'}): {opener}")
        return "\n".join(parts)
    except Exception:
        return "(no prior conversations on record)"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _feedback_examples(intent: str, max_examples: int = 3) -> str:
    """Return a few high-confidence past Q->A pairs that matched this intent.

    We pull from drafts where status='auto_sent' OR status='human_sent' AND there
    is a thumbs-up feedback row. The intent must match to keep examples relevant.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT d.draft_text, d.final_text, d.thread_id
               FROM drafts d
               LEFT JOIN feedback f ON f.draft_id = d.id
               WHERE d.intent = ?
                 AND d.status IN ('auto_sent', 'human_sent')
                 AND (f.thumbs IS NULL OR f.thumbs >= 0)
               ORDER BY d.id DESC
               LIMIT ?""",
            (intent, max_examples),
        ).fetchall()
        if not rows:
            return "(no prior approved examples for this intent yet)"
        out: list[str] = []
        for r in rows:
            sent = r["final_text"] or r["draft_text"]
            out.append(f"- (thread {r['thread_id']}) reply sent: {sent[:200]}")
        return "\n".join(out)
    except Exception:
        return "(no prior approved examples for this intent yet)"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def build_context(
    thread: ThreadInput,
    intent: str,
    *,
    top_k_wiki: int = 3,
    follow_cross_refs: bool = True,
) -> BuiltContext:
    """Assemble the full context window. Returns both the rendered prompt and
    the individual sections so the dashboard can show them later.
    """
    latest = _latest_customer_message(thread.messages)
    wiki_ctx = assemble_wiki_context(
        latest,
        top_k=top_k_wiki,
        intent=intent,
        follow_cross_refs=follow_cross_refs,
    )

    history_str = _format_thread_history(thread.messages)
    past_str = _past_conv_summary(thread.customer_username, thread.thread_id)
    fb_str = _feedback_examples(intent)

    prompt = CONTEXT_TEMPLATE.format(
        customer_display=thread.customer_display or thread.customer_username,
        customer_username=thread.customer_username,
        product_id=thread.product_id or "(unspecified)",
        intent=intent,
        thread_history=history_str,
        past_conv_summary=past_str,
        feedback_examples=fb_str,
        wiki_context=wiki_ctx or "(no wiki pages matched)",
        latest_message=latest or "(empty)",
    )

    sources_used = _extract_sources(wiki_ctx)

    return BuiltContext(
        intent=intent,
        latest_message=latest,
        wiki_context=wiki_ctx,
        thread_history=history_str,
        past_conv_summary=past_str,
        feedback_examples=fb_str,
        prompt=prompt,
        sources_used=sources_used,
    )


def _extract_sources(wiki_ctx: str) -> list[str]:
    """Pull out the `source:` markers so we can persist a list of cited pages."""
    out: list[str] = []
    for line in wiki_ctx.splitlines():
        if line.startswith("<!-- source:"):
            inner = line.replace("<!-- source:", "").replace("-->", "").strip()
            out.append(inner.split()[0])  # just the path, drop score/via tags
    return out
