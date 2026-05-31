"""Phase-2 smoke test.

Builds a synthetic refund thread, runs the full pipeline (intent -> context ->
draft), and prints:
  - the classified intent + reason
  - the drafted reply
  - the sources cited
  - the rows logged into token_usage for this draft

Run with:   py -m scripts.smoke_phase2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the project root importable when running as a script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.context_builder import ThreadInput
from agent.pipeline import run
from db.connection import get_connection, init_db


def main() -> int:
    # Make sure schema is in place (idempotent)
    init_db()

    thread = ThreadInput(
        thread_id="smoke-thread-001",
        customer_username="acme_jane",
        customer_display="Jane (Acme Co.)",
        product_id="invoiceflow",
        messages=[
            {"id": "m1", "role": "customer",
             "text": "Hi — we started using InvoiceFlow two weeks ago and it isn't a fit for our workflow."},
            {"id": "m2", "role": "admin",
             "text": "Sorry to hear that, Jane. What part isn't working for you?"},
            {"id": "m3", "role": "customer",
             "text": "The Stripe sync keeps double-counting refunds. I want a refund on our subscription please."},
        ],
    )

    result = run(thread)

    print("=" * 70)
    print(f"INTENT       : {result.intent.intent}  (confidence={result.intent.confidence:.2f})")
    print(f"INTENT REASON: {result.intent.reason}")
    print(f"INTENT MODEL : {result.intent.model}  cost=${result.intent.cost_usd:.6f}")
    print("=" * 70)
    print("SOURCES CITED:")
    for s in result.context.sources_used:
        print(f"  - {s}")
    print("=" * 70)
    print(f"DRAFT MODEL  : {result.draft.model}")
    print(f"DRAFT COST   : ${result.draft.cost_usd:.6f}  "
          f"(in={result.draft.input_tokens}, out={result.draft.output_tokens})")
    print(f"TOTAL COST   : ${result.total_cost_usd:.6f}")
    print("=" * 70)
    print("DRAFTED REPLY:")
    print(result.draft.text)
    print("=" * 70)

    # Verify token_usage rows landed
    conn = get_connection()
    rows = conn.execute(
        "SELECT step, model, input_tokens, output_tokens, cost_usd FROM token_usage WHERE draft_id = ?",
        (result.draft_id,),
    ).fetchall()
    conn.close()
    print(f"token_usage rows for draft_id={result.draft_id}:")
    for r in rows:
        print(f"  step={r['step']:<8} model={r['model']:<32} "
              f"in={r['input_tokens']:>5}  out={r['output_tokens']:>5}  cost=${r['cost_usd']:.6f}")
    print("=" * 70)

    # Soft assertions
    failed = []
    if result.intent.intent != "refund":
        failed.append(f"expected intent=refund, got {result.intent.intent}")
    if not result.draft.text:
        failed.append("draft text is empty")
    if not rows:
        failed.append("no token_usage rows were logged")
    if len(rows) < 2:
        failed.append(f"expected >= 2 token_usage rows (intent + draft), got {len(rows)}")

    if failed:
        print("FAILED:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("OK — Phase 2 pipeline working end-to-end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
