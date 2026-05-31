"""Intent router — classifies an incoming customer message into one of five
labels using the cheap Haiku model.

Output schema: {"intent": str, "confidence": float, "reason": str}
Valid labels: greeting | pricing | refund | technical | other
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from agent.llm_client import call
from config import settings
from prompts import INTENT_SYSTEM

log = logging.getLogger(__name__)

VALID_INTENTS = {"greeting", "pricing", "refund", "technical", "other"}


@dataclass
class IntentResult:
    intent: str
    confidence: float
    reason: str
    model: str
    cost_usd: float


def _build_user_turn(latest_message: str, prior_history: Optional[list[dict]] = None) -> str:
    parts: list[str] = []
    if prior_history:
        parts.append("PRIOR CONVERSATION:")
        for m in prior_history[-6:]:  # last 6 turns is plenty for routing
            role = m.get("role", "?")
            parts.append(f"  [{role}] {m.get('text','').strip()}")
        parts.append("")
    parts.append("LATEST CUSTOMER MESSAGE:")
    parts.append(latest_message.strip())
    return "\n".join(parts)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse(text: str) -> dict:
    """Tolerant JSON parse — model occasionally wraps in fences or trailing prose."""
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError(f"No JSON object in intent response: {text[:200]}")
    return json.loads(m.group(0))


def classify(
    latest_message: str,
    prior_history: Optional[list[dict]] = None,
    draft_id: Optional[int] = None,
) -> IntentResult:
    user = _build_user_turn(latest_message, prior_history)
    result = call(
        model=settings.intent_model,
        system=INTENT_SYSTEM,
        user=user,
        step="intent",
        max_tokens=120,
        temperature=0.0,
        draft_id=draft_id,
    )
    try:
        parsed = _parse(result.text)
        intent = str(parsed.get("intent", "other")).strip().lower()
        if intent not in VALID_INTENTS:
            log.warning("Intent classifier returned unknown label %r — falling back to 'other'", intent)
            intent = "other"
        confidence = float(parsed.get("confidence", 0.5))
        reason = str(parsed.get("reason", ""))[:200]
    except Exception as e:
        log.warning("Failed to parse intent response (%s) — falling back to 'other'. Raw: %r", e, result.text)
        intent, confidence, reason = "other", 0.0, f"parse_error: {e}"

    return IntentResult(
        intent=intent,
        confidence=confidence,
        reason=reason,
        model=result.model,
        cost_usd=result.cost_usd,
    )
