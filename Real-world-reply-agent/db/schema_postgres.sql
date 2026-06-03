-- LumenX Auto-Reply Agent — PostgreSQL schema (production).
-- Mirror of schema.sql with Postgres-flavored types (SERIAL ids, no PRAGMA).
-- Applied statement-by-statement by db/connection.py:init_db().

-- Threads mirrored from LumenX (lightweight cache + local linkage)
CREATE TABLE IF NOT EXISTS threads (
    id              TEXT PRIMARY KEY,
    username        TEXT,
    display_name    TEXT,
    product_id      TEXT,
    intent          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_synced_at  TIMESTAMP
);

-- Every customer / admin / agent message we've observed
CREATE TABLE IF NOT EXISTS messages (
    id              SERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL REFERENCES threads(id),
    remote_msg_id   TEXT,
    role            TEXT NOT NULL,            -- 'customer' | 'admin' | 'agent'
    text            TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (thread_id, remote_msg_id)
);

-- One draft produced per pipeline run; status tracks human-review flow
CREATE TABLE IF NOT EXISTS drafts (
    id              SERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL REFERENCES threads(id),
    intent          TEXT,
    draft_text      TEXT NOT NULL,
    final_text      TEXT,                     -- what was actually sent (after edit)
    confidence      REAL,
    status          TEXT NOT NULL,            -- 'pending_review' | 'auto_sent' | 'human_sent' | 'rejected'
    context_window  TEXT,                     -- JSON dump of context used for this draft
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_thread ON drafts(thread_id);

-- Explicit human feedback on drafts (thumbs / correction text)
CREATE TABLE IF NOT EXISTS feedback (
    id              SERIAL PRIMARY KEY,
    draft_id        INTEGER NOT NULL REFERENCES drafts(id),
    thumbs          INTEGER,                  -- 1 = up, -1 = down, NULL = none
    correction      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Every LLM call we make, for cost tracking
CREATE TABLE IF NOT EXISTS token_usage (
    id              SERIAL PRIMARY KEY,
    draft_id        INTEGER REFERENCES drafts(id),
    model           TEXT NOT NULL,
    step            TEXT NOT NULL,            -- 'intent' | 'draft' | 'summarize' | ...
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        REAL NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_draft ON token_usage(draft_id);
