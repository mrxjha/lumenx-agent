"""Phase-5 smoke test — offline poller.

Replaces the LumenX client with a fake one that yields a synthetic inbox + a
synthetic thread. Runs Poller.tick() once, asserts:
  - a draft was created
  - confidence was scored
  - status routed correctly
  - in dry-run mode, no POST happened

Run with:   py -m scripts.smoke_phase5
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.poller import Poller
from db.connection import get_connection, init_db


THREAD_ID = "smoke5-thread-001"


class FakeLumenXClient:
    """Stand-in that mimics the subset of LumenXClient the poller calls."""

    def __init__(self):
        self.base_url = "fake://lumenx"
        self.posted_replies: list[dict] = []

    def get_inbox(self, since=None):
        return {
            "server_time": "2026-05-27T12:00:00.000Z",
            "awaiting_count": 1,
            "entries": [
                {
                    "awaiting_admin": True,
                    "thread": {"id": THREAD_ID},
                }
            ],
        }

    def get_thread(self, thread_id):
        return {
            "id": thread_id,
            "username": "smoke_jane",
            "display_name": "Jane (Smoke)",
            "product_id": "invoiceflow",
            "messages": [
                {"id": "m1", "role": "customer",
                 "text": "Hi — what's the refund window for InvoiceFlow?"},
            ],
        }

    def post_reply(self, thread_id, text, draft_source="agent", confidence=None):
        self.posted_replies.append({
            "thread_id": thread_id, "text": text,
            "draft_source": draft_source, "confidence": confidence,
        })
        return {"ok": True}


def _wipe_smoke_rows():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM drafts WHERE thread_id = ?", (THREAD_ID,))
        conn.execute("DELETE FROM messages WHERE thread_id = ?", (THREAD_ID,))
        conn.execute("DELETE FROM threads WHERE id = ?", (THREAD_ID,))
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    init_db()
    _wipe_smoke_rows()

    fake = FakeLumenXClient()
    poller = Poller(client=fake, interval_sec=1, dry_run=True)

    print("=" * 70)
    print("STEP 1 — tick with synthetic inbox (dry-run):")
    results = poller.tick()

    if len(results) != 1:
        print(f"FAILED: expected 1 pipeline result, got {len(results)}")
        return 1
    r = results[0]
    print(f"  thread_id   : {r.thread_id}")
    print(f"  intent      : {r.intent.intent}  ({r.intent.confidence:.2f})")
    print(f"  draft_id    : {r.draft_id}")
    print(f"  confidence  : score={r.confidence.score} decision={r.confidence.decision}")
    print(f"  status      : {r.status}")
    print(f"  total cost  : ${r.total_cost_usd:.6f}")
    print()
    print("DRAFTED REPLY:")
    print(r.draft.text)
    print()

    if fake.posted_replies:
        print(f"FAILED: dry-run should not call post_reply but got {len(fake.posted_replies)} call(s)")
        return 1
    print("  OK — dry-run did not POST anything.")

    print()
    print("STEP 2 — re-tick: same latest message should be skipped (dedupe).")
    results2 = poller.tick()
    if results2:
        print(f"FAILED: expected 0 results on second tick (already processed), got {len(results2)}")
        return 1
    print("  OK — second tick skipped the already-processed thread.")

    print()
    print("STEP 3 — verify DB state.")
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, status, confidence, intent FROM drafts WHERE thread_id = ?",
            (THREAD_ID,),
        ).fetchall()
        token_rows = conn.execute(
            "SELECT step FROM token_usage WHERE draft_id = ?", (r.draft_id,)
        ).fetchall()
    finally:
        conn.close()

    if len(rows) != 1:
        print(f"FAILED: expected exactly 1 draft row, got {len(rows)}")
        return 1
    print(f"  drafts row    : status={rows[0]['status']} confidence={rows[0]['confidence']} intent={rows[0]['intent']}")
    steps = sorted(r["step"] for r in token_rows)
    print(f"  token_usage   : {steps}")
    if "intent" not in steps or "draft" not in steps:
        print("FAILED: expected both 'intent' and 'draft' token_usage rows")
        return 1

    print()
    print("OK — Phase 5 poller working end-to-end (offline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
