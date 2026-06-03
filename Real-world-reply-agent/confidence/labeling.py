"""Pulls (draft, final, intent, retrieval_hits) tuples from the database and
assigns binary labels suitable for training the Confidence Net.

Label rules (in priority order):
  - explicit thumbs feedback wins:    +1 -> label=1,  -1 -> label=0
  - status='rejected'                 -> label=0
  - status='auto_sent' and final null -> label=1 (the agent sent the draft itself)
  - edit_distance_norm(draft, final) < EDIT_NEAR_DUP_THRESHOLD  -> label=1
  - edit_distance_norm(draft, final) >= EDIT_HEAVY_THRESHOLD     -> label=0
  - otherwise                                                    -> SKIP (ambiguous)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from confidence.features import (
    FeatureInputs,
    FEATURE_NAMES,
    edit_distance_norm,
    extract,
)
from db.connection import get_connection


log = logging.getLogger(__name__)

EDIT_NEAR_DUP_THRESHOLD = 0.10   # < this and unchanged -> "human sent as-is"
EDIT_HEAVY_THRESHOLD = 0.30      # >= this -> "heavily edited / rewritten"


@dataclass
class LabeledRow:
    draft_id: int
    features: np.ndarray
    label: int                    # 0 or 1
    reason: str


def _retrieval_hits_from_context(context_window_json: Optional[str]) -> int:
    """Pipeline stored `sources_used` inside the context_window JSON. Use its
    length as the retrieval-hits feature (number of cited pages)."""
    if not context_window_json:
        return 0
    try:
        ctx = json.loads(context_window_json)
        return len(ctx.get("sources_used") or [])
    except Exception:
        return 0


def _assign_label(
    *,
    draft_text: str,
    final_text: Optional[str],
    status: str,
    thumbs: Optional[int],
) -> Optional[tuple[int, str]]:
    """Returns (label, reason) or None if the row is too ambiguous to use."""
    if thumbs == 1:
        return 1, "thumbs_up"
    if thumbs == -1:
        return 0, "thumbs_down"

    if status == "rejected":
        return 0, "status_rejected"

    if status == "auto_sent":
        # The agent sent its own draft; treat as positive evidence.
        return 1, "auto_sent"

    if final_text is None:
        # No human action yet — can't label.
        return None

    dist = edit_distance_norm(draft_text or "", final_text)
    if dist < EDIT_NEAR_DUP_THRESHOLD:
        return 1, f"near_duplicate({dist:.2f})"
    if dist >= EDIT_HEAVY_THRESHOLD:
        return 0, f"heavily_edited({dist:.2f})"

    return None  # in-between edits are ambiguous


def collect_labeled_rows() -> list[LabeledRow]:
    """Walk the drafts table and return every row that can be confidently
    labeled. Joins feedback so explicit thumbs win over edit-distance."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT d.id, d.intent, d.draft_text, d.final_text, d.status,
                      d.context_window,
                      (SELECT thumbs FROM feedback f WHERE f.draft_id = d.id
                       ORDER BY f.id DESC LIMIT 1) AS thumbs
               FROM drafts d
               WHERE d.draft_text <> ''
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[LabeledRow] = []
    skipped = 0
    for r in rows:
        outcome = _assign_label(
            draft_text=r["draft_text"],
            final_text=r["final_text"],
            status=r["status"],
            thumbs=r["thumbs"],
        )
        if outcome is None:
            skipped += 1
            continue
        label, reason = outcome
        vec = extract(FeatureInputs(
            draft=r["draft_text"],
            intent=r["intent"] or "other",
            retrieval_hits=_retrieval_hits_from_context(r["context_window"]),
        ))
        out.append(LabeledRow(draft_id=r["id"], features=vec, label=label, reason=reason))

    log.info("collected %d labeled rows (skipped %d ambiguous)", len(out), skipped)
    return out


def to_matrices(rows: Iterable[LabeledRow]) -> tuple[np.ndarray, np.ndarray]:
    rows = list(rows)
    if not rows:
        return (
            np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32),
            np.zeros((0,), dtype=np.int64),
        )
    X = np.stack([r.features for r in rows], axis=0)
    y = np.asarray([r.label for r in rows], dtype=np.int64)
    return X, y
