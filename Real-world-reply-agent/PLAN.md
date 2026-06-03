# Execution Plan — Real-World Auto-Reply Agent (Telegram)

Checklist version of the phases in CLAUDE.md. The platform is a **Telegram bot**
(lowest-setup real platform); knowledge domain is **Ramco products**. Most code
ports from `../Auto-reply-agent` unchanged.

---

## Phase 0 — Bot token (your only manual step, ~2 min)

1. Open **Telegram** (app or web) and search for **`@BotFather`** (the official
   one has a blue verified check).
2. Send **`/newbot`**.
3. Give it a **name** (display name, e.g. `Ramco Product Assistant`).
4. Give it a **username** ending in `bot` (must be unique, e.g. `ramco_assist_bot`).
5. BotFather replies with a **token** like `123456789:AAE...`. Copy it.
6. Paste it into `.env` as `TELEGRAM_BOT_TOKEN=...` (also add your `ANTHROPIC_API_KEY`).
7. Verify:
   ```powershell
   cd "Real-world-reply-agent"
   python -m venv .venv; .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m tg.client      # -> "Bot OK: @your_bot ..."
   ```

> No OAuth, no consent screen, no admin. The token is the only credential.
> `tg/client.py` (connector) is already written. ✅

## Phase 1 — Knowledge & seed data

- [ ] `wiki/builder.py` — scrape Ramco public product/docs pages → `wiki/products/*.md`
      (one page per product line) + `wiki/company_policy.md`.
- [ ] `scripts/seed_history.py` — generate realistic Ramco (question → ideal reply)
      pairs → seed the feedback log + the labeled MLP training set.

## Phase 2 — Pipeline (port, mostly unchanged)

- [ ] Copy `agent/` + `prompts/`; repoint context_builder at the new wiki +
      chat history; tweak `draft_system.md` for Ramco tone + short chat replies.
- [ ] Confirm token/cost logging writes to `token_usage`.

## Phase 3 — Confidence Net (bootstrap, no live cold-start wait)

- [ ] Build features over the seed set; label by closeness to the ideal reply.
- [ ] `confidence/train.py` → `model.pkl`. Check class balance + held-out score.
- [ ] Retrain on real dashboard approve/edit labels as chats accumulate.

## Phase 4 — Dashboard (reuse)

- [ ] Streamlit: review queue with confidence, approve/edit/reject, thumbs feedback,
      cost panel, expandable "Show Context Window" per reply.

## Phase 5 — Auto-reply loop

- [ ] `agent/poller.py` runs `tg.get_updates()` long-poll → `pipeline.py` →
      route: (AUTO_SEND_ENABLED and score ≥ θ) → `send_message`; else → review queue.
- [ ] Persist the last update_id (`data/tg_offset.json`) so updates aren't reprocessed.

## Phase 6 — Deploy (reuse Railway setup)

- [ ] Reuse Dockerfile / `railway.toml` / `entrypoint.sh` + `SERVICE_ROLE`
      (poller | dashboard | web), Postgres via `DATABASE_URL`.
- [ ] Set env vars per service. Generate a public domain for the dashboard.

## Phase 7 — Deliverables

- [ ] **Screen recording (5–10 min):** DM the bot → agent drafts a reply → show
      confidence score + routing → show dashboard cost/context.
- [ ] **PDF report (2–3 pages):** build process + accuracy / speed / cost /
      hallucination rate (numbers from the dashboard + a held-out test set).
- [ ] **Hosted link:** submit the Railway dashboard URL via the Microsoft Form.

---

## Risks / gotchas

- **One poller only.** Telegram `getUpdates` rejects concurrent pollers ("Conflict:
  terminated by other getUpdates request") and clashes with a set webhook. Run a
  single poller service; don't also register a webhook.
- **Offset hygiene.** Always call getUpdates with `offset = last_update_id + 1` and
  persist it, or you'll reprocess old messages on restart.
- **Auto-send safety.** Keep `AUTO_SEND_ENABLED=false` until the MLP is trusted.
- **Public data only.** Wiki is built from public Ramco pages; never use real work
  data. Never use nikhilkumar.jha@ramco.com anywhere.
- **Railway.** Reuse the documented `SERVICE_ROLE` + `${PORT}`-in-`sh -c` fixes from
  the LumenX deploy — don't reintroduce those bugs.
