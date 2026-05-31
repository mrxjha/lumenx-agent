-- LumenX Auto-Reply Agent — local DB schema
-- SQLite. Run via db/init_db.py.

PRAGMA foreign_keys = ON;

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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL,
    remote_msg_id   TEXT,
    role            TEXT NOT NULL,            -- 'customer' | 'admin' | 'agent'
    text            TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (thread_id, remote_msg_id),
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);

-- One draft produced per pipeline run; status tracks human-review flow
CREATE TABLE IF NOT EXISTS drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL,
    intent          TEXT,
    draft_text      TEXT NOT NULL,
    final_text      TEXT,                     -- what was actually sent (after edit)
    confidence      REAL,
    status          TEXT NOT NULL,            -- 'pending_review' | 'auto_sent' | 'human_sent' | 'rejected'
    context_window  TEXT,                     -- JSON dump of context used for this draft
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at         TIMESTAMP,
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_thread ON drafts(thread_id);

-- Explicit human feedback on drafts (thumbs / correction text)
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL,
    thumbs          INTEGER,                  -- 1 = up, -1 = down, NULL = none
    correction      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

-- Every LLM call we make, for cost tracking
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER,                  -- nullable: some calls don't tie to a draft
    model           TEXT NOT NULL,
    step            TEXT NOT NULL,            -- 'intent' | 'draft' | 'summarize' | ...
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        REAL NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_draft ON token_usage(draft_id);
