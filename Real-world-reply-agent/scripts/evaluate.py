"""Offline evaluation harness — produces the metrics used in the project report.

Runs a fixed, balanced Ramco test set through the full pipeline and measures:
  - intent-router accuracy (predicted vs expected label)
  - end-to-end latency per reply
  - cost per reply, split by the cheap router (Haiku) vs the drafter (Sonnet)
  - hallucination rate on sensitive intents (pricing/refund must NOT state a
    concrete price/number — they must deflect)

Run with:  python -m scripts.evaluate
Writes data/eval_results.json and prints a summary.
"""
from __future__ import annotations

import json
import re
import statistics
import time

from agent.context_builder import ThreadInput
from agent.pipeline import run
from config import PROJECT_ROOT
from db.connection import init_db

TESTS = [
    ("pricing",   "How much does Ramco ERP cost?"),
    ("pricing",   "What is the per-user license fee for Ramco HCM?"),
    ("refund",    "Can I get a refund if we cancel the contract early?"),
    ("refund",    "What is your money-back policy?"),
    ("technical", "Does Ramco Aviation support airworthiness tracking?"),
    ("technical", "Can Ramco Logistics do route optimization?"),
    ("technical", "Does Ramco ERP expose a REST API for integration?"),
    ("other",     "Where is Ramco Systems headquartered?"),
    ("greeting",  "Hi there"),
    ("greeting",  "Thanks for the help!"),
]

# A concrete money figure / rate stated as fact = potential hallucination on a
# sensitive intent (the agent is supposed to deflect, never quote a price).
PRICE_RE = re.compile(r"\$\s?\d|\b\d+\s?%|\bper\s+(user|month|employee|seat)\b|\bUSD\s?\d|\b\d{2,}\s?(?:usd|dollars)\b", re.I)


def hallucinated(intent: str, text: str) -> bool:
    return intent in ("pricing", "refund") and bool(PRICE_RE.search(text))


def main() -> int:
    init_db()
    rows = []
    for expected, q in TESTS:
        t0 = time.perf_counter()
        thread = ThreadInput(
            thread_id=f"eval-{abs(hash(q)) % 100000}",
            customer_username="evaluator", customer_display="Evaluator",
            product_id=None, messages=[{"id": "m1", "role": "customer", "text": q}],
        )
        res = run(thread)
        dt = time.perf_counter() - t0
        h = hallucinated(res.intent.intent, res.draft.text)
        rows.append({
            "q": q, "expected": expected, "intent": res.intent.intent,
            "intent_ok": res.intent.intent == expected,
            "confidence": res.confidence.score, "status": res.status,
            "latency_s": round(dt, 3),
            "intent_cost": round(res.intent.cost_usd, 6),
            "draft_cost": round(res.draft.cost_usd, 6),
            "cost_usd": round(res.total_cost_usd, 6),
            "halluc": h, "draft": res.draft.text,
        })
        print(f"[{dt:5.2f}s] {expected:9s}->{res.intent.intent:9s} "
              f"conf={res.confidence.score} ${res.total_cost_usd:.5f} halluc={h}  {q}")

    lat = [r["latency_s"] for r in rows]
    cost = [r["cost_usd"] for r in rows]
    icost = [r["intent_cost"] for r in rows]
    dcost = [r["draft_cost"] for r in rows]
    sensitive = [r for r in rows if r["expected"] in ("pricing", "refund")]
    halluc_n = sum(r["halluc"] for r in sensitive)

    print("\n==================== SUMMARY ====================")
    print(f"queries                : {len(rows)}")
    print(f"intent accuracy        : {sum(r['intent_ok'] for r in rows)}/{len(rows)} "
          f"= {sum(r['intent_ok'] for r in rows)/len(rows):.0%}")
    print(f"latency  avg / median  : {statistics.mean(lat):.2f}s / {statistics.median(lat):.2f}s "
          f"(min {min(lat):.2f}, max {max(lat):.2f})")
    print(f"cost/reply avg         : ${statistics.mean(cost):.5f}")
    print(f"  router (Haiku) avg   : ${statistics.mean(icost):.6f}")
    print(f"  drafter (Sonnet) avg : ${statistics.mean(dcost):.6f}")
    print(f"sensitive queries      : {len(sensitive)} (pricing/refund)")
    print(f"hallucinated (price)   : {halluc_n}  -> rate {halluc_n/len(sensitive):.0%}")

    out = {
        "summary": {
            "n": len(rows),
            "intent_accuracy": sum(r["intent_ok"] for r in rows) / len(rows),
            "latency_avg_s": statistics.mean(lat),
            "latency_median_s": statistics.median(lat),
            "latency_min_s": min(lat), "latency_max_s": max(lat),
            "cost_avg_usd": statistics.mean(cost),
            "router_cost_avg_usd": statistics.mean(icost),
            "drafter_cost_avg_usd": statistics.mean(dcost),
            "sensitive_n": len(sensitive),
            "hallucinated_n": halluc_n,
            "hallucination_rate": halluc_n / len(sensitive) if sensitive else 0.0,
        },
        "rows": rows,
    }
    (PROJECT_ROOT / "data" / "eval_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nwrote data/eval_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
