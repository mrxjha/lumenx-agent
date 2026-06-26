# LumenX Auto-Reply Agent

An intelligent, cost-aware customer support agent for the LumenX SaaS platform. It drafts replies to incoming customer messages, scores them with a Confidence Net, auto-sends high-confidence replies, and routes uncertain ones to a human review dashboard.  

---
                       
## What This System Does                                                                                    

1. **Polls** the LumenX admin inbox for unanswered customer threads
2. **Classifies intent** (greeting, pricing, technical, refund, other) using a cheap fast model
3. **Builds context** from product knowledge, past conversations, the LLM wiki, and the feedback log
4. **Drafts a reply** using a high-quality LLM with anti-hallucination guardrails
5. **Scores the draft** with a Tiny MLP (Confidence Net, 0–1)
6. **Routes**: confidence ≥ threshold → auto-send | confidence < threshold → human review
7. **Logs everything**: tokens used, cost per reply, full context window, human edits

---

## Architecture                                                                                                                                          

```
Incoming Message
    │
    ▼
Intent Router  ──────────────────────────────────────────────┐
(haiku model)                                                 │
    │ intent label                                            │ generic greeting/
    ▼                                                         │ off-topic → polite
Context Builder                                              │ direct reply
  ├─ Products JSON  (/api/admin/products)                     │
  ├─ LLM Wiki       (wiki/products/*.md)                     │
  ├─ Current thread (full history)                           │
  ├─ Past-conv summary (all past threads)                    │
  └─ Feedback log   (past approved Q→A pairs)                │
    │                                                         │
    ▼                                                         │
LLM Draft  (sonnet-4-6)                                      │
  • Anti-hallucination system prompt                         │
  • Never invents pricing or refund details                  │
    │                                                         │
    ▼                                                         │
Confidence Net  (Tiny MLP, local inference)                  │
  • Features: len_ratio, intent, retrieval_hits,             │
    edit_distance, semantic_similarity                       │
    │                                                         │
    ├── score ≥ θ ──→  AUTO-SEND                             │
    │                                                        │
    └── score < θ ──→  Human Review Queue ──→ Edit/Approve ──┘
                            │
                     Feedback logged
                     (trains next MLP)
```

---

## Key Features

### Anti-Hallucination Guardrails
The agent is instructed never to fabricate pricing tiers, refund windows, or cancellation policies. If the information is not in the loaded context, it responds with a polite acknowledgment rather than guessing.

### Confidence Net (Tiny MLP                                                             
A small two-hidden-layer neural network decides whether to auto-send a reply or route it to a human. It learns from the team's own edit history — every time a human approves, edits, or rejects a draft, that signal updates future predictions.

**Ground truth collection strategy** — since there is no labeled data at project start:
- All replies go to human review for the first ~150 interactions (cold-start phase)
- Drafts that the human sends with minimal edits (edit distance < 10%) are labeled as high-confidence
- Heavily edited or rejected drafts are labeled as low-confidence
- After 150 samples the MLP is trained; threshold is set conservatively at 0.95 and gradually lowered

### LLM Wiki
Product knowledge is fetched from the LumenX API and rendered into structured Markdown pages (one per product). The wiki is refreshed nightly. At reply time, relevant pages are selected by intent and keyword match and injected into the context window.

### Feedback Loop
Every reply — whether auto-sent or human-reviewed — feeds back into:
- The feedback log (good Q→A examples surfaced to future agents)
- The MLP training data
- The past-conversation summary (context for future replies)

### Cost Dashboard
A Streamlit dashboard shows:
- Cost per reply (input + output tokens × model pricing)
- Daily and weekly totals
- Model breakdown (haiku vs sonnet)
- Expandable "Show Context Window" per reply
- Confidence score and routing decision per reply

---

## Project Phases

| Phase | Deliverable | Status |
|---|---|---|
| 1 — Foundation | Project scaffold, API client, DB schema, LLM Wiki builder + cross-refs + graph UI | Done |
| 2 — Core Pipeline | Intent Router, Context Builder, LLM Draft, token-usage logging | Done |
| 3 — Confidence Net | Feature extractor, label assigner, MLP train + predict, pipeline integration | Done |
| 4 — Review Dashboard | Streamlit UI: review queue, cost dashboard, context window expander | Done |
| 5 — Auto-Reply Loop | Inbox poller, auto-send path, dashboard → LumenX POST | Done |
| 6 — Deployment | Dockerfile, railway.toml, Procfile, /healthz endpoint, env docs | Done |

---

## Setup

### Prerequisites
- Python 3.11+
- Anthropic API key
- LumenX admin token

### Quick Start

```bash
git clone <this-repo>
cd lumenx-agent

pip install -r requirements.txt

cp .env.example .env
# Fill in ANTHROPIC_API_KEY and LUMENX_ADMIN_TOKEN

# Initialize the local SQLite schema
python -m db.connection

# Build the LLM Wiki from current product data
python -m wiki.builder

# Three processes (each in its own terminal):

# 1. Wiki + knowledge-graph UI + /healthz
uvicorn server.app:app --port 8000

# 2. Human review dashboard
streamlit run dashboard/app.py

# 3. Auto-reply poller
python -m agent.poller            # production loop
python -m agent.poller --once     # single sweep, for testing
python -m agent.poller --dry-run  # never POST replies back
```

### Train the Confidence Net

The MLP needs labeled drafts (sent vs edited vs rejected). After ~30+ drafts have flowed through the dashboard:

```bash
python -m confidence.train
# writes confidence/model.pkl — predict.py picks it up automatically
```

### Deploy to Railway

```bash
railway up
```

`railway.toml` deploys three services from the same Docker image: `web` (wiki UI + healthz), `dashboard` (Streamlit), and `poller` (background loop). Set the env vars from `.env.example` in the Railway dashboard or via `railway variables set`.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | required |
| `LUMENX_ADMIN_TOKEN` | LumenX admin bearer token | required |
| `CONFIDENCE_THRESHOLD` | Auto-send threshold (0–1) | `0.90` |
| `POLL_INTERVAL_SEC` | Inbox poll frequency in seconds | `10` |
| `DATABASE_URL` | SQLite or Postgres URL | `sqlite:///data/agent.db` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

---

## Model Cost Reference

| Step | Model | Approx. Cost per Call |
|---|---|---|
| Intent classification | claude-haiku-4-5 | ~$0.0002 |
| Reply drafting | claude-sonnet-4-6 | ~$0.003–$0.015 |
| Confidence Net inference | Local MLP | $0.00 |

All actual costs are tracked per reply in the database and visible in the dashboard.

---

## LumenX Platform

- **GitHub**: https://github.com/VizuaraAI/lumenx
- **Deployed Site**: https://lumenx-demo.up.railway.app
- **Customer Chat**: https://lumenx-demo.up.railway.app/chat                                                
- **Admin UI**: https://lumenx-demo.up.railway.app/admin                            

---

## Repository Structure

```
lumenx-agent/
├── agent/              # Pipeline orchestrator, inbox poller, intent router
├── confidence/         # MLP feature extraction, training, inference
├── lumenx/             # LumenX API client
├── wiki/               # LLM Wiki builder and product markdown pages
├── prompts/            # System prompt library
├── dashboard/          # Streamlit review + cost dashboard
├── db/                 # Schema definitions
├── data/               # SQLite database (gitignored)
├── CLAUDE.md           # Claude Code context file
└── README.md           # This file
```
