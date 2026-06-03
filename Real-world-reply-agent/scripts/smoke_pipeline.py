"""Local end-to-end pipeline test — no Telegram required.

Useful because the Ramco network blocks Telegram: this feeds a synthetic message
straight through intent -> context -> draft -> confidence -> route and prints the
result, so you can validate the agent locally and watch its anti-hallucination
behaviour without the bot.

Run with:  python -m scripts.smoke_pipeline "How much does Ramco ERP cost?"
"""
from __future__ import annotations

import logging
import sys

from agent.context_builder import ThreadInput
from agent.pipeline import run
from db.connection import init_db


def ask(question: str) -> None:
    thread = ThreadInput(
        thread_id="smoke-local",
        customer_username="tester",
        customer_display="Tester",
        product_id=None,
        messages=[{"id": f"m-{abs(hash(question)) % 100000}", "role": "customer", "text": question}],
    )
    res = run(thread)
    print("\n" + "=" * 70)
    print("Q:", question)
    print(f"intent     : {res.intent.intent} ({res.intent.confidence:.2f}) — {res.intent.reason}")
    score = f"{res.confidence.score:.3f}" if res.confidence.score is not None else "n/a"
    print(f"confidence : {score}  decision={res.confidence.decision}  -> status={res.status}")
    print(f"cost_usd   : {res.total_cost_usd:.6f}")
    print(f"sources    : {res.context.sources_used}")
    print("--- draft ---")
    print(res.draft.text)


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    init_db()
    args = sys.argv[1:]
    questions = [" ".join(args)] if args else [
        "How much does Ramco ERP cost per user?",
        "Does Ramco Aviation support airworthiness tracking?",
    ]
    for q in questions:
        ask(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
