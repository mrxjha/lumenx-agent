"""Phase-3 smoke test — Confidence Net cold-start + train + predict.

No LLM calls. Synthesizes labeled draft rows directly in the DB, exercises
training, then re-runs prediction.

Run with:   py -m scripts.smoke_phase3
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confidence import predict as predict_pkg  # not used as module
from confidence.predict import predict, model_loaded, reset as reset_predictor
from confidence.train import MODEL_PATH, save, train
from db.connection import get_connection, init_db


GOOD_DRAFT = (
    "Thanks for reaching out. InvoiceFlow's Pro plan is $39/month per workspace "
    "[invoiceflow]. Refunds are issued within 14 days of first purchase [company_policy], "
    "so you're well within the window. I'll process this today.\n— LumenX Support"
)

BAD_DRAFT_HEDGE = (
    "Hi, thanks for the message. I don't have access to that information right now — "
    "let me check with the team and get back to you.\n— LumenX Support"
)


def _wipe_smoke_rows():
    """Remove any rows from prior smoke runs so we get a clean train set."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM drafts WHERE thread_id LIKE 'smoke3-%'")
        conn.execute("DELETE FROM threads WHERE id LIKE 'smoke3-%'")
        conn.commit()
    finally:
        conn.close()


def _insert_thread(conn, tid):
    conn.execute(
        """INSERT INTO threads (id, username, display_name, product_id, intent)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (id) DO NOTHING""",
        (tid, "synth_user", "Synth User", "invoiceflow", "pricing"),
    )


def _insert_labeled_drafts(n_good: int = 25, n_bad: int = 25):
    """Synthesize labeled rows. Status + final_text combinations chosen so that
    labeling.py classifies them deterministically."""
    rng = random.Random(0)
    conn = get_connection()
    try:
        for i in range(n_good):
            tid = f"smoke3-good-{i}"
            _insert_thread(conn, tid)
            draft = GOOD_DRAFT + (" Extra word." if i % 3 == 0 else "")
            ctx = {"sources_used": ["company_policy.md", "products/invoiceflow.md"]}
            conn.execute(
                """INSERT INTO drafts
                   (thread_id, intent, draft_text, final_text, status, context_window, confidence)
                   VALUES (?, 'pricing', ?, ?, 'human_sent', ?, NULL)""",
                (tid, draft, draft, json.dumps(ctx)),  # final == draft -> near-duplicate
            )
        for i in range(n_bad):
            tid = f"smoke3-bad-{i}"
            _insert_thread(conn, tid)
            ctx = {"sources_used": []}  # 0 retrieval hits — risky
            conn.execute(
                """INSERT INTO drafts
                   (thread_id, intent, draft_text, final_text, status, context_window, confidence)
                   VALUES (?, 'pricing', ?, ?, 'rejected', ?, NULL)""",
                (tid, BAD_DRAFT_HEDGE, None, json.dumps(ctx)),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    init_db()
    _wipe_smoke_rows()

    # Remove any stale model from previous runs so we exercise cold-start
    if MODEL_PATH.exists():
        MODEL_PATH.unlink()
    reset_predictor()

    print("=" * 70)
    print("STEP 1 — cold start (no model.pkl):")
    cs = predict(GOOD_DRAFT, intent="pricing", retrieval_hits=2)
    print(f"  score={cs.score}  decision={cs.decision}  reason={cs.reason}")
    assert cs.score is None, "Expected None when no model is trained"
    assert cs.decision == "review (no_model)"
    print("  OK — cold-start routes everything to review.")

    print()
    print("STEP 2 — synthesize labeled rows (25 good / 25 bad) and train:")
    _insert_labeled_drafts(25, 25)
    artifact = train(min_samples=30)
    save(artifact)
    print(f"  trained on {artifact.n_samples} samples")
    print(f"  metrics: {artifact.metrics}")

    print()
    print("STEP 3 — reload model + score the good vs bad drafts:")
    reset_predictor()
    assert model_loaded(), "model should be loaded after save+reset"

    good = predict(GOOD_DRAFT, intent="pricing", retrieval_hits=2)
    bad  = predict(BAD_DRAFT_HEDGE, intent="pricing", retrieval_hits=0)

    print(f"  good draft -> score={good.score:.3f}  decision={good.decision}")
    print(f"  bad  draft -> score={bad.score:.3f}  decision={bad.decision}")

    failed = []
    if good.score is None or bad.score is None:
        failed.append("Expected non-None scores after training.")
    elif good.score <= bad.score:
        failed.append(f"Expected good > bad, got good={good.score:.3f} bad={bad.score:.3f}")
    if good.decision not in ("auto_send", "review"):
        failed.append(f"Unexpected decision label: {good.decision}")

    print()
    if failed:
        print("FAILED:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("OK — Phase 3 confidence net: cold-start -> trained -> routed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
