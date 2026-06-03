"""Seed the DB with synthetic Ramco Q&A so the Confidence Net can train BEFORE
any real Telegram traffic exists (the cold-start bootstrap).

A Telegram bot can't scrape historical chats, so instead of "scraping past
replies" we generate realistic (question, draft, final-reply) triples. Each
becomes a `drafts` row; `confidence/labeling.py` derives a binary label from
edit-distance(draft, final) (or status/thumbs):

  draft ~= final           -> label 1  (good draft, would be sent as-is)
  draft heavily edited      -> label 0  (needed human rewrite)
  status 'rejected'         -> label 0

The NEGATIVE drafts deliberately reproduce the failure modes we care about —
invented prices/numbers, no citations, no sign-off, over-promising — while the
POSITIVE drafts are grounded, cited, and signed off. The MLP then learns
"drafts that hallucinate pricing or skip citations need human review".

Run with:  python -m scripts.seed_history            # insert (clears prior seed first)
           python -m scripts.seed_history --keep      # append without clearing
"""
from __future__ import annotations

import argparse
import json

from db.connection import get_connection, init_db

SIGN = "\n— Ramco Support"

# (id, display name, capability phrase, functional area)
PRODUCTS = [
    ("ramco_erp", "Ramco ERP", "multi-currency general ledger", "finance and supply chain"),
    ("ramco_hcm", "Ramco HCM & Global Payroll", "multi-country payroll", "core HR and payroll"),
    ("ramco_aviation", "Ramco Aviation", "airworthiness and MRO tracking", "aircraft maintenance"),
    ("ramco_logistics", "Ramco Logistics", "route optimization and proof-of-delivery", "3PL operations"),
    ("ramco_eam", "Ramco EAM", "predictive maintenance", "asset management"),
]


def _scenarios() -> list[dict]:
    rows: list[dict] = []

    for pid, name, feat, area in PRODUCTS:
        good = (f"Yes — {name} supports {feat} as part of its {area} capabilities [{pid}]. "
                f"Happy to show your team how it fits your setup.{SIGN}")
        bad = (f"Absolutely! {name} does {feat} and guarantees 99.99% uptime with a "
               f"30-day money-back guarantee on a $199/month plan.")
        # technical capability: good (label 1) + hallucinated (label 0)
        rows.append(dict(intent="technical", q=f"Does {name} support {feat}?",
                         draft=good, final=good, status="human_sent", thumbs=1, sources=[pid]))
        rows.append(dict(intent="technical", q=f"Can {name} handle {feat}?",
                         draft=bad, final=good, status="human_sent", thumbs=None, sources=[pid]))

        ig = (f"{name} exposes a REST web services API to integrate {area} with your existing "
              f"systems [{pid}]. I can share the integration guide with your team.{SIGN}")
        igbad = (f"Easy — {name} has a free unlimited API, a 100% uptime SLA, and onboarding "
                 f"costs exactly $5,000 flat.")
        # integration how-to: good + hallucinated SLA/price
        rows.append(dict(intent="technical", q=f"How do I integrate {name} with our systems?",
                         draft=ig, final=ig, status="human_sent", thumbs=1, sources=[pid]))
        rows.append(dict(intent="technical", q=f"Does {name} have an API and what does it cost?",
                         draft=igbad, final=ig, status="human_sent", thumbs=None, sources=[pid]))

        pgood = (f"Ramco doesn't publish standard pricing for {name} — it's tailored to your "
                 f"requirements [company_policy]. I'll connect you with our team for a quote.{SIGN}")
        pbad = f"{name} starts at $499 per user per month, and you get 20% off if you pay annually."
        # pricing: good deflection (label 1) + invented price (label 0)
        rows.append(dict(intent="pricing", q=f"How much does {name} cost?",
                         draft=pgood, final=pgood, status="human_sent", thumbs=1, sources=["company_policy"]))
        rows.append(dict(intent="pricing", q=f"What's the price of {name} per user?",
                         draft=pbad, final=pgood, status="rejected", thumbs=-1, sources=[]))

    # Refund / contract
    rgood = ("Refund and cancellation terms follow your signed agreement [company_policy] — I'll "
             f"connect you with your account manager to review the specifics.{SIGN}")
    rbad = "Sure, we offer a no-questions-asked 30-day money-back refund on all Ramco products."
    for q in ["Can I get a refund?", "How do I cancel my subscription?", "What's your refund policy?",
              "Can we terminate the contract early?"]:
        rows.append(dict(intent="refund", q=q, draft=rgood, final=rgood,
                         status="human_sent", thumbs=1, sources=["company_policy"]))
    rows.append(dict(intent="refund", q="I want my money back now.",
                     draft=rbad, final=rgood, status="rejected", thumbs=-1, sources=[]))
    rows.append(dict(intent="refund", q="Do you do refunds within 30 days?",
                     draft=rbad, final=rgood, status="human_sent", thumbs=None, sources=[]))

    # Greetings
    ggood = f"Hello! How can I help you with Ramco products today?{SIGN}"
    for q in ["Hi", "Hello there", "Hey, good morning", "Thanks for the help!", "Hi team", "gm"]:
        rows.append(dict(intent="greeting", q=q, draft=ggood, final=ggood,
                         status="human_sent", thumbs=1, sources=[]))

    # Other / general info
    ogood = ("Ramco Systems is headquartered in Chennai, India, with customers in 150+ countries "
             f"[company_policy]. Is there something specific I can help you with?{SIGN}")
    for q in ["Where is Ramco located?", "Tell me about your company", "Which industries do you serve?",
              "Do you have offices outside India?"]:
        rows.append(dict(intent="other", q=q, draft=ogood, final=ogood,
                         status="human_sent", thumbs=None, sources=["company_policy"]))
    obad = "We are the #1 ERP company in the world and we beat SAP and Oracle on every single benchmark."
    rows.append(dict(intent="other", q="Are you better than SAP?",
                     draft=obad, final=ogood, status="rejected", thumbs=-1, sources=[]))

    return rows


def _reset(conn) -> None:
    conn.execute("DELETE FROM feedback WHERE draft_id IN (SELECT id FROM drafts WHERE thread_id LIKE 'seed-%')")
    conn.execute("DELETE FROM drafts WHERE thread_id LIKE 'seed-%'")
    conn.execute("DELETE FROM messages WHERE thread_id LIKE 'seed-%'")
    conn.execute("DELETE FROM threads WHERE id LIKE 'seed-%'")
    conn.commit()


def insert(rows: list[dict], reset: bool = True) -> int:
    init_db()
    conn = get_connection()
    try:
        if reset:
            _reset(conn)
        for i, r in enumerate(rows, 1):
            tid = f"seed-{i:04d}"
            conn.execute(
                """INSERT INTO threads (id, username, display_name, product_id, intent)
                   VALUES (?, 'seed_user', 'Seed Customer', NULL, ?)
                   ON CONFLICT(id) DO UPDATE SET intent = excluded.intent""",
                (tid, r["intent"]),
            )
            conn.execute(
                """INSERT INTO messages (thread_id, remote_msg_id, role, text)
                   VALUES (?, ?, 'customer', ?)
                   ON CONFLICT (thread_id, remote_msg_id) DO NOTHING""",
                (tid, f"{tid}-q", r["q"]),
            )
            cur = conn.execute(
                """INSERT INTO drafts (thread_id, intent, draft_text, final_text, confidence,
                                       status, context_window)
                   VALUES (?, ?, ?, ?, NULL, ?, ?)
                   RETURNING id""",
                (tid, r["intent"], r["draft"], r["final"], r["status"],
                 json.dumps({"sources_used": r["sources"]})),
            )
            did = cur.fetchone()["id"]
            if r.get("thumbs") is not None:
                conn.execute(
                    "INSERT INTO feedback (draft_id, thumbs, correction) VALUES (?, ?, NULL)",
                    (did, r["thumbs"]),
                )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="Append without clearing prior seed rows.")
    args = parser.parse_args()

    rows = _scenarios()
    n = insert(rows, reset=not args.keep)
    print(f"[ok] inserted {n} seed drafts.")

    # Report the resulting label distribution so you can see class balance.
    try:
        from confidence.labeling import collect_labeled_rows
        labeled = collect_labeled_rows()
        pos = sum(1 for r in labeled if r.label == 1)
        neg = sum(1 for r in labeled if r.label == 0)
        print(f"[ok] labelable rows: {len(labeled)}  (label 1: {pos}, label 0: {neg})")
        print("     next: python -m confidence.train")
    except Exception as e:
        print(f"[warn] could not compute label distribution: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
