"""Reply drafter — calls Sonnet with the anti-hallucination system prompt and
the assembled context. Returns the draft text + token-usage stats."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agent.context_builder import BuiltContext
from agent.llm_client import call
from config import settings
from prompts import DRAFT_SYSTEM


@dataclass
class DraftResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def draft_reply(ctx: BuiltContext, draft_id: Optional[int] = None) -> DraftResult:
    """Produce a reply draft for the customer based on the built context.

    `draft_id` is optional: if the caller has pre-inserted a row into `drafts`,
    pass its id so the token_usage row links back. Phase 5 will pre-insert,
    Phase 2 smoke tests pass None.
    """
    result = call(
        model=settings.draft_model,
        system=DRAFT_SYSTEM,
        user=ctx.prompt,
        step="draft",
        max_tokens=800,
        temperature=0.3,
        draft_id=draft_id,
    )
    return DraftResult(
        text=result.text.strip(),
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )
