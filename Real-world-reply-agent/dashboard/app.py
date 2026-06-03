"""Ramco Auto-Reply Agent (Telegram) — human-review + cost dashboard.

Run with:  streamlit run dashboard/app.py

Pages:
  - Review Queue   : edit/approve/reject pending drafts, leave thumbs feedback
  - Cost Dashboard : per-model / per-step / daily cost view
  - Activity Log   : recent replies with linked token usage
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from config import settings
from dashboard.db_helpers import (
    approve_draft,
    fetch_draft,
    fetch_pending_drafts,
    fetch_recent_replies_df,
    fetch_summary_counts,
    fetch_thread_messages,
    fetch_token_usage_df,
    parse_context_window,
    reject_draft,
)


st.set_page_config(
    page_title="Ramco Agent — Review & Cost",
    page_icon="✨",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("Ramco Agent")
page = st.sidebar.radio(
    "View",
    ("Review Queue", "Cost Dashboard", "Activity Log"),
    label_visibility="collapsed",
)

counts = fetch_summary_counts()
st.sidebar.markdown("### Status")
st.sidebar.metric("Pending review", counts.get("pending_review", 0))
st.sidebar.metric("Auto-sent",      counts.get("auto_sent", 0))
st.sidebar.metric("Human-sent",     counts.get("human_sent", 0))
st.sidebar.metric("Rejected",       counts.get("rejected", 0))

st.sidebar.markdown("---")
st.sidebar.caption(f"Confidence threshold: **{settings.confidence_threshold:.2f}**")
st.sidebar.caption(f"Draft model: `{settings.draft_model}`")
st.sidebar.caption(f"Intent model: `{settings.intent_model}`")
st.sidebar.caption(f"Auto-send: **{'ON' if settings.auto_send_enabled else 'OFF (review all)'}**")


# ---------------------------------------------------------------------------
# Page: Review Queue
# ---------------------------------------------------------------------------

def _confidence_badge(score: float | None) -> str:
    if score is None:
        return "n/a"
    if score >= settings.confidence_threshold:
        emoji = "🟢"
    elif score >= 0.6:
        emoji = "🟡"
    else:
        emoji = "🔴"
    return f"{emoji} {score:.2f}"


def render_review_queue() -> None:
    st.title("Review Queue")
    st.caption("Drafts waiting on a human. Edit if needed, then approve or reject.")

    drafts = fetch_pending_drafts()
    if not drafts:
        st.success("Inbox zero — no drafts pending review.")
        return

    st.write(f"**{len(drafts)}** draft(s) pending review.")

    for d in drafts:
        score = d.get("confidence")
        header = (
            f"**#{d['id']}** · thread `{d['thread_id']}` · "
            f"{d.get('display_name') or d.get('username') or 'unknown'} · "
            f"intent: `{d['intent'] or '?'}` · confidence: {_confidence_badge(score)}"
        )
        with st.expander(header, expanded=False):
            _render_one_draft(d)


def _render_one_draft(d: dict) -> None:
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown("##### Drafted reply")
        edited = st.text_area(
            label="Edit the draft if needed, then approve.",
            value=d["draft_text"] or "",
            height=240,
            key=f"draft_text_{d['id']}",
            label_visibility="collapsed",
        )
        thumbs = st.radio(
            "Feedback",
            options=("(no thumbs)", "👍 thumbs up", "👎 thumbs down"),
            key=f"thumbs_{d['id']}",
            horizontal=True,
        )
        correction = st.text_input(
            "Correction note (optional)",
            key=f"correction_{d['id']}",
        )

        thumbs_value = {"👍 thumbs up": 1, "👎 thumbs down": -1}.get(thumbs)

        c1, c2, c3, _ = st.columns([1, 1, 1, 3])
        send_remote = c3.checkbox("Send via Telegram", value=True, key=f"send_remote_{d['id']}")
        if c1.button("✓ Approve & send", key=f"approve_{d['id']}", type="primary"):
            sent, err = approve_draft(d["id"], edited, thumbs_value, correction, send_to_telegram=send_remote)
            if err:
                st.error(f"Saved locally, but Telegram send failed: {err}")
            elif sent:
                st.success(f"Draft #{d['id']} approved and sent via Telegram.")
            else:
                st.success(f"Draft #{d['id']} approved (local only).")
            st.rerun()
        if c2.button("✗ Reject", key=f"reject_{d['id']}"):
            reject_draft(d["id"], correction)
            st.warning(f"Draft #{d['id']} rejected.")
            st.rerun()

    with col_r:
        st.markdown("##### Customer thread")
        messages = fetch_thread_messages(d["thread_id"])
        if not messages:
            st.caption("(no thread messages mirrored locally)")
        for m in messages:
            badge = {"customer": "🧑", "admin": "🧑‍💼", "agent": "🤖"}.get(m["role"], "•")
            st.markdown(f"{badge} **{m['role']}** — {m['text']}")

    # Context window expander
    ctx = parse_context_window(d.get("context_window"))
    if ctx:
        with st.expander("Show context window used for this draft"):
            st.markdown(f"**Detected intent:** `{ctx.get('intent','?')}`")
            st.markdown(f"**Wiki chars loaded:** {ctx.get('wiki_chars', 0)}")
            sources = ctx.get("sources_used") or []
            if sources:
                st.markdown("**Sources cited:**")
                for s in sources:
                    st.markdown(f"- `{s}`")
            conf = ctx.get("confidence") or {}
            if conf:
                st.markdown(
                    f"**Confidence:** score={conf.get('score')}, "
                    f"threshold={conf.get('threshold')}, decision=`{conf.get('decision')}`"
                )
                st.caption(conf.get("reason", ""))
            with st.expander("Thread history snapshot"):
                st.text(ctx.get("thread_history") or "(empty)")
            with st.expander("Past conversations summary"):
                st.text(ctx.get("past_conv_summary") or "(empty)")
            with st.expander("Feedback examples seeded"):
                st.text(ctx.get("feedback_examples") or "(empty)")


# ---------------------------------------------------------------------------
# Page: Cost Dashboard
# ---------------------------------------------------------------------------

def render_cost_dashboard() -> None:
    st.title("Cost Dashboard")
    st.caption("Every Anthropic call is logged. Costs computed from the price table in `agent/llm_client.py`.")

    df = fetch_token_usage_df()
    if df.empty:
        st.info("No LLM calls logged yet. Once the pipeline runs, this page will populate.")
        return

    total_cost = float(df["cost_usd"].sum())
    total_in = int(df["input_tokens"].sum())
    total_out = int(df["output_tokens"].sum())
    total_calls = len(df)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cost",   f"${total_cost:.4f}")
    c2.metric("LLM calls",    f"{total_calls}")
    c3.metric("Input tokens", f"{total_in:,}")
    c4.metric("Output tokens",f"{total_out:,}")

    st.markdown("### By model")
    by_model = (
        df.groupby("model", as_index=False)
          .agg(calls=("id", "count"),
               input_tokens=("input_tokens", "sum"),
               output_tokens=("output_tokens", "sum"),
               cost_usd=("cost_usd", "sum"))
          .sort_values("cost_usd", ascending=False)
    )
    st.dataframe(by_model, use_container_width=True, hide_index=True)

    st.markdown("### By pipeline step")
    by_step = (
        df.groupby("step", as_index=False)
          .agg(calls=("id", "count"),
               cost_usd=("cost_usd", "sum"),
               avg_input=("input_tokens", "mean"),
               avg_output=("output_tokens", "mean"))
          .sort_values("cost_usd", ascending=False)
    )
    st.dataframe(by_step, use_container_width=True, hide_index=True)

    st.markdown("### Cost per day")
    daily = (
        df.assign(day=df["created_at"].dt.date)
          .groupby("day", as_index=True)["cost_usd"].sum()
          .sort_index()
    )
    if len(daily) > 0:
        st.line_chart(daily)

    st.markdown("### Per-reply cost (last 50)")
    replies = fetch_recent_replies_df(limit=50)
    if not replies.empty:
        replies["cost_usd"] = replies["cost_usd"].round(6)
        st.dataframe(replies, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page: Activity Log
# ---------------------------------------------------------------------------

def render_activity_log() -> None:
    st.title("Activity Log")
    st.caption("Recent drafts across all statuses, with linked cost and confidence.")
    df = fetch_recent_replies_df(limit=200)
    if df.empty:
        st.info("No drafts yet.")
        return
    # round for display
    if "cost_usd" in df.columns:
        df["cost_usd"] = df["cost_usd"].fillna(0).round(6)
    if "confidence" in df.columns:
        df["confidence"] = df["confidence"].round(3)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "Review Queue":
    render_review_queue()
elif page == "Cost Dashboard":
    render_cost_dashboard()
elif page == "Activity Log":
    render_activity_log()
