# LumenX Auto-Reply Agent — CLAUDE.md

This file is the persistent context file for Claude Code. Every session working on this project should start here.

---

## Project Goal

Build a production-ready **Auto-Reply LLM Agent** for the LumenX SaaS customer chat platform. The agent:
1. Receives incoming customer messages via the LumenX API
2. Routes them through an intent classifier
3. Builds a rich context window (products, past conversations, LLM wiki, feedback log)
4. Drafts a reply using Claude
5. Scores the draft with a Tiny MLP (Confidence Net)
6. Auto-sends high-confidence replies; queues low-confidence ones for human review
7. Logs everything for cost tracking and continuous MLP retraining

---

## System Architecture

```
Incoming Message
    │
    ▼
┌─────────────────┐
│  Intent Router  │  ← claude-haiku (cheap)
│ greeting /      │    classify: greeting | pricing | technical | refund | other
│ pricing /       │
│ technical / ... │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│              Context Builder                │
│  • Products JSON  (from /api/admin/products)│
│  • LLM Wiki       (wiki/products/*.md)      │
│  • Current thread (full message history)   │
│  • Past-conv summary  (all past threads)   │
│  • Feedback log   (past good Q→A pairs)    │
└────────┬────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│   LLM Draft     │  ← claude-sonnet-4-6 (quality)
│  (candidate     │    anti-hallucination system prompt
│   reply)        │    tracks input/output tokens
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Confidence Net │  ← Tiny MLP (sklearn, local inference)
│  (score 0→1)    │    features: len_ratio, intent_type,
│                 │    retrieval_hits, edit_dist, sem_sim
└────────┬────────┘
         │
    ┌────┴────┐
    │ Router  │
    └────┬────┘
   score ≥ θ │ score < θ
         │         │
         ▼         ▼
    auto-send   human review queue → edit/approve → send
         │         │
         └────┬────┘
              ▼
         Feedback log (label for next MLP training round)
```

---

## Tech Stack Decisions

| Component | Choice | Reason |
|---|---|---|
| Intent classification | `claude-haiku-4-5-20251001` | Cheapest, fast, sufficient for routing |
| Reply drafting | `claude-sonnet-4-6` | High quality, good instruction following |
| Confidence MLP | `sklearn.MLPClassifier` | Lightweight, no GPU needed, fast inference |
| Backend | FastAPI (Python) | Async, clean OpenAPI docs, easy Railway deploy |
| Dashboard | Streamlit | Rapid build, good for internal tools |
| Database | SQLite → PostgreSQL | SQLite for dev, migrate to Postgres for prod |
| Deployment | Railway | LumenX is already on Railway |

---

## Phase Plan

### Phase 1 — Foundation (Scaffold + Data Layer)
- [ ] Project folder structure and `requirements.txt`
- [ ] `.env.example` with all required env vars
- [ ] LumenX API client (`lumenx/client.py`) — wraps all admin endpoints
- [ ] SQLite schema: `threads`, `messages`, `drafts`, `feedback`, `token_usage`
- [ ] LLM Wiki builder: fetch `/api/admin/products`, render to `wiki/products/*.md`
- [ ] Karpathy-style wiki loader for context injection

### Phase 2 — Core Pipeline
- [ ] Intent Router: system prompt + haiku call → returns intent label
- [ ] Context Builder: assembles all context sources into a structured prompt
- [ ] LLM Draft: sonnet call with anti-hallucination system prompt
- [ ] Token logging middleware (log every API call: model, in_tokens, out_tokens, cost_usd)
- [ ] System prompt library (`prompts/`)

### Phase 3 — Confidence Net (MLP)
- [ ] Feature extractor (`confidence/features.py`)
- [ ] Cold-start data collection mode (log all draft+human-edit pairs)
- [ ] Label assignment logic (edit_distance_norm threshold)
- [ ] MLP training script (`confidence/train.py`)
- [ ] MLP inference wrapper (`confidence/predict.py`)
- [ ] Threshold config via env var `CONFIDENCE_THRESHOLD` (default 0.90)

### Phase 4 — Human Review Dashboard
- [ ] Streamlit app (`dashboard/app.py`)
- [ ] Review queue: list pending threads with confidence score
- [ ] Reply editor: show draft, allow edit, approve/reject
- [ ] Feedback buttons (thumbs up / thumbs down + free-text correction)
- [ ] Cost dashboard: tokens per reply, model breakdown, daily/weekly totals
- [ ] Expandable "Show Context Window" per reply

### Phase 5 — Auto-Reply Loop
- [ ] Inbox poller (`agent/poller.py`): polls `/api/admin/inbox` every N seconds
- [ ] Full pipeline orchestrator (`agent/pipeline.py`)
- [ ] Auto-send path: POST to `/api/admin/threads/{id}/reply`
- [ ] Human-review path: insert into `drafts` table, dashboard picks it up
- [ ] Configurable polling interval via `POLL_INTERVAL_SEC`

### Phase 6 — Deployment
- [ ] Dockerfile + `railway.toml`
- [ ] Environment variable documentation
- [ ] Health check endpoint
- [ ] Production SQLite → PostgreSQL migration path

---

## Key Constraints (Never Violate)

1. **No hallucination on pricing or refunds.** System prompt must instruct the model to say "I don't have access to that information right now" if the answer is not in the loaded context.
2. **No hardcoded secrets.** Admin token and API keys go in `.env` only.
3. **Every API call is logged.** `token_usage` table captures model, prompt_tokens, completion_tokens, cost_usd, reply_id, timestamp.
4. **Confidence threshold is user-configurable.** Default 0.90, stored in env/config, shown in dashboard.

---

## LumenX API Reference

```
Base URL  : https://lumenx-demo.up.railway.app
Auth      : X-Admin-Token: <LUMENX_ADMIN_TOKEN>

GET  /api/admin/inbox?since=ISO       Poll for unanswered threads
GET  /api/admin/threads/{id}          Full thread with all messages
POST /api/admin/threads/{id}/reply    Send reply  { text, draft_source, confidence }
GET  /api/admin/products              All 20 products (pricing, refund, features)
GET  /api/admin/export                Full data dump for training context
GET  /api/admin/stats                 Counts and intent distribution
```

---

## LLM Wiki Strategy (Karpathy Gist Approach)

Reference: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

- Fetch all products from `/api/admin/products` on startup
- Render each product into a structured markdown file: `wiki/products/{id}.md`
- Each wiki page contains: description, pricing tiers, features, refund policy, integrations, target audience, support SLA
- At context-build time, load relevant wiki pages based on intent + keyword match
- Keep a single `wiki/company_policy.md` for cross-product policies (refund window, trial, discounts)
- Wiki is regenerated nightly to stay fresh

---

## Confidence Net: Ground Truth Collection Strategy

**The cold-start problem**: No labeled data at project start.

**Solution — Bootstrap Phase (first ~150 replies):**
1. Run agent in **human-review-only mode** (all drafts routed to review queue)
2. Human approves/edits/rejects each draft
3. Auto-label: `confidence = 1` if `edit_distance_norm < 0.10` (human sent nearly as-is); `confidence = 0` if heavily edited or rejected
4. Also capture explicit thumbs feedback as labels

**MLP Features:**
- `len_ratio`: `len(draft) / len(final_sent_reply)` — closer to 1 means less edited
- `intent_encoded`: one-hot of intent category (4 classes)
- `retrieval_hits`: number of wiki chunks that matched the query
- `edit_distance_norm`: Levenshtein distance / max(len(draft), len(final))
- `semantic_sim`: cosine similarity of draft vs final embeddings (use cheap embeddings or sentence-transformers)

**Training schedule:**
- First train: after 150 labeled samples, threshold set conservatively at 0.95
- Weekly retrain with all accumulated data
- Gradually lower threshold toward 0.90 as precision improves

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...
LUMENX_ADMIN_TOKEN=lmx_GQlch0Q5NOwVuVSADXRuFNJvxIpzVGwI
CONFIDENCE_THRESHOLD=0.90
POLL_INTERVAL_SEC=10
DATABASE_URL=sqlite:///data/agent.db
LOG_LEVEL=INFO
```

---

## Cost Model

| Operation | Model | Est. Cost |
|---|---|---|
| Intent classification | haiku-4-5 | ~$0.0002/call |
| Reply drafting | sonnet-4-6 | ~$0.003–0.015/call |
| MLP inference | local | $0 |

Target: track actual cost per reply in `token_usage` table and surface in dashboard.

---

## Folder Structure (Target)

```
lumenx-agent/
├── agent/
│   ├── pipeline.py        # orchestrates intent→context→draft→confidence→route
│   ├── poller.py          # inbox polling loop
│   ├── intent.py          # intent router (haiku)
│   ├── context_builder.py # assembles context window
│   └── llm_draft.py       # reply drafter (sonnet)
├── confidence/
│   ├── features.py        # feature extraction
│   ├── train.py           # MLP training script
│   └── predict.py         # inference wrapper
├── lumenx/
│   └── client.py          # LumenX API client
├── wiki/
│   ├── builder.py         # fetches products → markdown files
│   └── products/          # *.md wiki pages
├── prompts/
│   ├── intent_system.md
│   ├── draft_system.md    # anti-hallucination instructions
│   └── context_template.md
├── dashboard/
│   └── app.py             # Streamlit review + cost dashboard
├── db/
│   └── schema.sql
├── data/                  # SQLite file (gitignored)
├── .env.example
├── requirements.txt
├── Dockerfile
└── railway.toml
```
