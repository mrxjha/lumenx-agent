"""Feature extraction for the Confidence Net.

The MLP predicts P(draft would be sent as-is) BEFORE a human has touched it.
That constraint matters: features must be computable from the draft + the
pipeline state at inference time, NOT from the post-edit comparison.

So this module exposes two distinct things:

1. `extract(...)` — the actual MLP feature vector (draft-only signals).
2. Utilities `len_ratio`, `edit_distance_norm`, `semantic_sim` — used by
   `confidence/labeling.py` to derive training labels from (draft, final)
   pairs. They are NOT part of the feature vector.

Feature order is FROZEN. If you add one, append at the end and bump
SCHEMA_VERSION so old `model.pkl` files refuse to load.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import Levenshtein


SCHEMA_VERSION = 1

# One-hot order for intent. Keep stable; new intents go at the end.
INTENT_LABELS: tuple[str, ...] = ("greeting", "pricing", "refund", "technical", "other")


# ---------------------------------------------------------------------------
# Utilities used by labeling.py (NOT part of the inference feature vector)
# ---------------------------------------------------------------------------

def len_ratio(draft: str, final: str) -> float:
    if not final:
        return 0.0
    return float(min(len(draft) / max(len(final), 1), 3.0))


def edit_distance_norm(draft: str, final: str) -> float:
    """Levenshtein(draft, final) / max(len). 0 = identical, 1 = totally rewritten."""
    if not draft and not final:
        return 0.0
    denom = max(len(draft), len(final), 1)
    return float(Levenshtein.distance(draft, final) / denom)


_WS = re.compile(r"\s+")


def _trigrams(text: str) -> dict[str, int]:
    s = _WS.sub(" ", text.lower().strip())
    s = f"  {s}  "
    out: dict[str, int] = {}
    for i in range(len(s) - 2):
        tg = s[i:i + 3]
        out[tg] = out.get(tg, 0) + 1
    return out


def semantic_sim(a: str, b: str) -> float:
    """Char-trigram cosine in [0, 1]. Cheap proxy for sentence embeddings."""
    if not a or not b:
        return 0.0
    ta, tb = _trigrams(a), _trigrams(b)
    keys = set(ta) | set(tb)
    if not keys:
        return 0.0
    dot = sum(ta.get(k, 0) * tb.get(k, 0) for k in keys)
    na = sum(v * v for v in ta.values()) ** 0.5
    nb = sum(v * v for v in tb.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


# ---------------------------------------------------------------------------
# MLP feature vector (draft-only signals available at inference time)
# ---------------------------------------------------------------------------

# Hedge phrases the draft system prompt is allowed to emit when it doesn't
# know an answer. Their PRESENCE is informative — drafts that hedge are more
# likely to need human review.
HEDGE_PATTERNS = (
    r"don'?t have (access|that information)",
    r"let me check",
    r"i'?ll (have to|need to) (check|follow up|confirm)",
    r"get back to you",
    r"follow up with the team",
    r"reach out to (the )?team",
)

_HEDGE_RE = re.compile("|".join(HEDGE_PATTERNS), re.IGNORECASE)
_CITATION_RE = re.compile(r"\[[a-z0-9_]+\]")
_DIGIT_RE = re.compile(r"\d")
_SIGNOFF_RE = re.compile(r"—\s*Ramco Support\s*$", re.MULTILINE)


def intent_one_hot(intent: str) -> list[float]:
    intent = (intent or "other").lower()
    vec = [0.0] * len(INTENT_LABELS)
    for i, label in enumerate(INTENT_LABELS):
        if intent == label:
            vec[i] = 1.0
            return vec
    vec[INTENT_LABELS.index("other")] = 1.0
    return vec


@dataclass
class FeatureInputs:
    draft: str
    intent: str
    retrieval_hits: int


FEATURE_NAMES: tuple[str, ...] = (
    "draft_len_chars",
    "draft_word_count",
    "digit_count",
    "citation_count",
    "has_hedge_phrase",
    "has_signoff",
    "retrieval_hits",
    *[f"intent_{i}" for i in INTENT_LABELS],
)


def extract(inputs: FeatureInputs) -> np.ndarray:
    """1-D float32 vector of length len(FEATURE_NAMES). Pure function of the
    draft + intent + retrieval_hits. Computed identically at train and
    inference time."""
    draft = inputs.draft or ""
    vec: list[float] = [
        # raw length signals (the MLP can implicitly learn a length distribution)
        float(len(draft)),
        float(len(draft.split())),
        # risk signals
        float(len(_DIGIT_RE.findall(draft))),       # numbers = pricing risk
        float(len(_CITATION_RE.findall(draft))),    # more citations = more grounded
        1.0 if _HEDGE_RE.search(draft) else 0.0,    # explicit "I don't know" admission
        1.0 if _SIGNOFF_RE.search(draft) else 0.0,  # followed the format rule
        # context signal
        float(inputs.retrieval_hits),
    ]
    vec.extend(intent_one_hot(inputs.intent))
    return np.asarray(vec, dtype=np.float32)
