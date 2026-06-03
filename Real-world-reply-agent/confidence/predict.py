"""Inference wrapper for the Confidence Net.

Loads `confidence/model.pkl` (lazily, once) and returns a score in [0, 1] for
a given (draft, intent, retrieval_hits) tuple. Falls back gracefully when
no model has been trained yet — the cold-start state.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from confidence.features import (
    FEATURE_NAMES,
    FeatureInputs,
    SCHEMA_VERSION,
    extract,
)
from config import settings


log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "model.pkl"

_artifact = None
_load_error: Optional[str] = None


@dataclass
class ConfidenceScore:
    score: Optional[float]      # None when no model is loaded
    threshold: float
    decision: str               # 'auto_send' | 'review' | 'review (no_model)'
    reason: str


def _load() -> None:
    """Load the trained artifact if available. Cached. Safe to call repeatedly."""
    global _artifact, _load_error
    if _artifact is not None or _load_error is not None:
        return
    if not MODEL_PATH.exists():
        _load_error = "no_model"
        return
    try:
        with MODEL_PATH.open("rb") as f:
            artifact = pickle.load(f)
    except Exception as e:
        _load_error = f"unpickle_failed: {e}"
        log.warning("Could not unpickle %s: %s", MODEL_PATH, e)
        return

    if getattr(artifact, "schema_version", None) != SCHEMA_VERSION:
        _load_error = (
            f"schema_mismatch: model has v{getattr(artifact, 'schema_version', '?')}, "
            f"code has v{SCHEMA_VERSION}"
        )
        log.warning(_load_error)
        return
    if tuple(getattr(artifact, "feature_names", ())) != FEATURE_NAMES:
        _load_error = "feature_names_mismatch"
        log.warning(_load_error)
        return

    _artifact = artifact
    log.info("loaded confidence model trained_at=%s n=%d metrics=%s",
             artifact.trained_at, artifact.n_samples, artifact.metrics)


def reset() -> None:
    """Drop the cached model — call after retraining."""
    global _artifact, _load_error
    _artifact = None
    _load_error = None


def model_loaded() -> bool:
    _load()
    return _artifact is not None


def predict(draft: str, intent: str, retrieval_hits: int) -> ConfidenceScore:
    threshold = settings.confidence_threshold
    _load()
    if _artifact is None:
        return ConfidenceScore(
            score=None,
            threshold=threshold,
            decision="review (no_model)",
            reason=_load_error or "no_model",
        )

    vec = extract(FeatureInputs(draft=draft, intent=intent, retrieval_hits=retrieval_hits))
    X = _artifact.scaler.transform(vec.reshape(1, -1))
    # predict_proba columns are aligned with model.classes_; pick P(class == 1)
    proba = _artifact.model.predict_proba(X)[0]
    classes = list(_artifact.model.classes_)
    if 1 in classes:
        p1 = float(proba[classes.index(1)])
    else:
        # Degenerate model that only ever saw one class
        p1 = float(proba[0]) if classes == [1] else 0.0

    decision = "auto_send" if p1 >= threshold else "review"
    return ConfidenceScore(
        score=p1,
        threshold=threshold,
        decision=decision,
        reason=f"P(send_as_is)={p1:.3f}, threshold={threshold:.2f}",
    )
