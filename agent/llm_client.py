"""Single entry point for all Anthropic calls in the agent pipeline.

Every call goes through `call()`, which:
  1. Invokes anthropic.Anthropic.messages.create
  2. Computes USD cost from the price table
  3. Writes a row to `token_usage` so the dashboard / cost report can sum it later

Keep all model-aware pricing here. If Anthropic changes a price, update one dict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from anthropic import Anthropic

from config import settings
from db.connection import get_connection

log = logging.getLogger(__name__)

# USD per 1M tokens. Source: Anthropic public pricing as of project start.
# Keep this table the single source of truth for cost arithmetic.
PRICES: dict[str, tuple[float, float]] = {
    # model_id : (input_per_mtok, output_per_mtok)
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5":          (1.00, 5.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-sonnet-4-5":         (3.00, 15.00),
    "claude-opus-4-7":           (15.00, 75.00),
}


def _price_for(model: str) -> tuple[float, float]:
    if model in PRICES:
        return PRICES[model]
    # Pricing fallback for unlisted models — prefix match on family
    for key, price in PRICES.items():
        if model.startswith(key):
            return price
    log.warning("No price entry for model %s — logging cost as 0.0", model)
    return (0.0, 0.0)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _price_for(model)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stop_reason: Optional[str] = None


_client: Optional[Anthropic] = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured in .env")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _log_usage(
    *,
    model: str,
    step: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    draft_id: Optional[int],
) -> None:
    """Persist one row to token_usage. Best-effort: never raise into the caller."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO token_usage
                   (draft_id, model, step, input_tokens, output_tokens, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (draft_id, model, step, input_tokens, output_tokens, cost_usd),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("Failed to log token_usage row: %s", e)


def call(
    *,
    model: str,
    system: str,
    user: str,
    step: str,
    max_tokens: int = 800,
    temperature: float = 0.2,
    draft_id: Optional[int] = None,
) -> LLMResult:
    """Single point of entry for every LLM call. Logs usage automatically."""
    client = _get_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = estimate_cost_usd(resp.model, in_tok, out_tok)

    _log_usage(
        model=resp.model,
        step=step,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        draft_id=draft_id,
    )

    return LLMResult(
        text=text,
        model=resp.model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        stop_reason=getattr(resp, "stop_reason", None),
    )
