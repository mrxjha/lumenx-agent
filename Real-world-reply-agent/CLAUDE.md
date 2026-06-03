# Real-World Auto-Reply Agent (Telegram) — CLAUDE.md

Persistent context for Claude Code. Start every session here.

This project is the **real-world** version of the LumenX Auto-Reply Agent
(`../Auto-reply-agent`). **The pipeline is identical** — only the platform
connector (a **Telegram bot** instead of the LumenX demo API) and the knowledge
base (real **Ramco product** docs) change. Reuse the proven modules; don't rebuild them.

**Why Telegram:** lowest-setup real platform — auth is a single bot token from
`@BotFather`, with no OAuth, no consent screen, and no corporate/IT admin. (Email
and Teams were rejected; the @ramco.com work account is off-limits — it would
expose real work data in the publicly-showcased deliverables.) See the memory
note [[feedback_low_setup_platform]].

---

## Project Goal

A production auto-reply **Telegram bot** that answers **Ramco product** questions:
receive a chat message → classify intent → build a context window from the LLM
wiki (Ramco docs) + conversation history + a feedback log of past good Q→A →
draft a reply with Claude → score it with a tiny MLP → auto-send high-confidence
replies, queue low-confidence ones for human review → log cost on every call →
deploy on Railway.

---

## System Architecture

```
Telegram message (getUpdates long-poll)
    │
    ▼
 Intent Router (claude-haiku)  ── greeting/off-topic → polite canned reply, stop
    │  pricing | technical | how-to | other
    ▼
 Context Builder
   • LLM Wiki (wiki/products/*.md — scraped Ramco docs)
   • Current chat history (this chat_id)
   • Summary of past conversations (all chats)
   • Feedback log (past good Q→A pairs)
    │
    ▼
 LLM Draft (claude-sonnet-4-6) — anti-hallucination prompt, tracks tokens
    │
    ▼
 Confidence Net (sklearn MLP, local) — score 0..1
   features: len_ratio, intent_encoded, retrieval_hits, edit_dist_norm, sem_sim
    │
 ┌──┴── score ≥ θ AND AUTO_SEND_ENABLED ──┐      else
 ▼                                         ▼        ▼
 sendMessage (auto-reply)         human review queue (Streamlit)
 └──────────────────┬──────────────────────┘  → edit / approve → sendMessage
                    ▼
               Feedback log (labels for next MLP retrain)
```

`AUTO_SEND_ENABLED=false` forces **everything** to human review — the safe
default for cold-start and for the demo. Flip to `true` to enable the auto path.

---

## Reuse Map (port from ../Auto-reply-agent)

| Module | Action |
|---|---|
| `agent/intent.py`, `context_builder.py`, `llm_draft.py`, `llm_client.py`, `pipeline.py`, `poller.py` | **Reuse**; repoint poller at Telegram getUpdates + new data |
| `confidence/features.py`, `train.py`, `predict.py`, `labeling.py` | **Reuse**; retrain on seed + real feedback labels |
| `dashboard/app.py`, `db_helpers.py` | **Reuse** |
| `db/schema.sql`, `schema_postgres.sql`, `connection.py` | **Reuse** (chats/messages tables map cleanly to chat_id) |
| `prompts/*.md` | **Reuse**; tweak draft_system for Ramco tone + chat (short) format |
| `Dockerfile`, `railway.toml`, `entrypoint.sh` | **Reuse**; poller role runs the getUpdates loop |
| `lumenx/client.py` | **REPLACED** by `tg/client.py` (Telegram Bot API) ✅ done |
| `wiki/builder.py` | **REWRITE** — source = scraped Ramco docs, not products API |
| — | **NEW** `scripts/seed_history.py` (generate Ramco Q→A history + MLP seed) |

> Connector package is `tg/` not `telegram/` — `python-telegram-bot` ships a
> top-level `telegram` package that a local one would shadow. We use the raw Bot
> API over httpx.

---

## Tech Stack

| Component | Choice |
|---|---|
| Intent classification | `claude-haiku-4-5-20251001` (cheap) |
| Reply drafting | `claude-sonnet-4-6` (quality) |
| Confidence Net | `sklearn.MLPClassifier` (local, no GPU) |
| Platform connector | Telegram Bot API (raw HTTP via httpx) |
| Backend | FastAPI (health/landing) |
| Dashboard | Streamlit |
| DB | SQLite (dev) → PostgreSQL (Railway) |
| Deploy | Railway (`SERVICE_ROLE` multi-service, proven setup) |

Use the **claude-api** skill for Anthropic SDK work; enable prompt caching on the
wiki/history context block (large + reused across calls → big cost win).

---

## Phase Plan (build in phases — never one-shot)

- **Phase 0 — Bot token** (manual, ~2 min): create the bot via `@BotFather`
  (`/newbot`), paste the token into `.env`. Connector `tg/client.py` ✅ done;
  verify with `python -m tg.client`.
- **Phase 1 — Knowledge & seed data**: `wiki/builder.py` scrapes Ramco public
  product/docs pages → `wiki/products/*.md`; `scripts/seed_history.py` generates a
  realistic Ramco Q→A history → feedback log + the labeled MLP seed set.
- **Phase 2 — Pipeline**: port intent → context → draft → token/cost logging.
- **Phase 3 — Confidence Net**: train MLP on the seed set; retrain on real
  approve/edit feedback as chats accumulate.
- **Phase 4 — Dashboard**: review queue, confidence, cost, expandable context.
- **Phase 5 — Auto-reply loop**: `tg` getUpdates long-poll → pipeline → route
  (auto-send vs review), persist `tg_offset` so updates aren't re-processed.
- **Phase 6 — Deploy** on Railway (poller + dashboard + web health).
- **Phase 7 — Deliverables**: screen recording, PDF report, hosted link.

---

## Hard Constraints (never violate)

1. **No hallucination on pricing / contractual / licensing details.** If not in
   loaded context, say "I don't have access to that information right now."
2. **No secrets in code.** `TELEGRAM_BOT_TOKEN` + `ANTHROPIC_API_KEY` in env only.
3. **Log every LLM call** (model, in/out tokens, cost_usd, chat_id) to the
   `token_usage` table; surface in dashboard.
4. **Confidence threshold is user-configurable** (`CONFIDENCE_THRESHOLD`, default 0.90).
5. **Auto-send is opt-in** (`AUTO_SEND_ENABLED=false` default) — during cold-start
   and the demo, every reply goes through human review.
6. **Never use nikhilkumar.jha@ramco.com or any real Ramco work data.** Knowledge
   comes only from public Ramco product pages; chat history is synthetic seed +
   real bot conversations.

---

## Environment Variables

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...        # from @BotFather
AUTO_SEND_ENABLED=false       # true | false
CONFIDENCE_THRESHOLD=0.90
POLL_INTERVAL_SEC=30          # getUpdates long-poll timeout
INTENT_MODEL=claude-haiku-4-5-20251001
DRAFT_MODEL=claude-sonnet-4-6
DATABASE_URL=sqlite:///data/agent.db
SERVICE_ROLE=poller           # poller | dashboard | web (Railway)
WIKI_DIR=wiki/products
LOG_LEVEL=INFO
```

---

## Deliverables → where the numbers come from

| Report metric | Source |
|---|---|
| Accuracy | % of agent drafts the human approved unedited (edit_distance_norm < 0.10) |
| Speed | per-stage latency logged in pipeline (intent, draft, total) |
| Cost | `token_usage` table → dashboard cost panel |
| Hallucination rate | held-out test set: count fabrications vs correct "no access" deflections |

Demo flow for the recording: DM the bot a Ramco question → show the draft + its
confidence score in the dashboard → approve/auto-send → show cost + expandable
context window.
