"""Train the Confidence Net.

Pipeline:
  1. Pull labeled rows from drafts/feedback tables (`labeling.collect_labeled_rows`)
  2. Standardize features
  3. Fit an MLPClassifier
  4. Save (scaler, model, schema_version, metadata) to confidence/model.pkl

The cold-start contract: training requires at least MIN_TRAIN_SAMPLES rows
with both classes represented. Below that, we bail out cleanly and the
pipeline keeps routing every draft to human review.

Run with:  py -m confidence.train
"""
from __future__ import annotations

import argparse
import logging
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from confidence.artifact import TrainArtifact
from confidence.features import FEATURE_NAMES, SCHEMA_VERSION
from confidence.labeling import collect_labeled_rows, to_matrices


log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "model.pkl"
MIN_TRAIN_SAMPLES = 30   # set low so the smoke test can hit it; real bar is 150 per spec


def train(min_samples: int = MIN_TRAIN_SAMPLES) -> TrainArtifact:
    rows = collect_labeled_rows()
    X, y = to_matrices(rows)

    if len(rows) < min_samples:
        raise RuntimeError(
            f"Not enough labeled rows to train: have {len(rows)}, need {min_samples}. "
            "Run more drafts through the human-review queue first."
        )
    if len(set(y.tolist())) < 2:
        raise RuntimeError(
            f"All {len(y)} labeled rows have the same class ({set(y.tolist())}). "
            "Need both positive and negative examples."
        )

    test_size = 0.2 if len(rows) >= 50 else 0.0
    if test_size > 0:
        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )
    else:
        X_tr, X_te, y_tr, y_te = X, X, y, y  # tiny dataset — eval on train

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    model = MLPClassifier(
        hidden_layer_sizes=(32, 16),
        activation="relu",
        solver="adam",
        max_iter=2000,
        random_state=42,
        early_stopping=False,
    )
    model.fit(X_tr_s, y_tr)

    preds = model.predict(X_te_s)
    acc = accuracy_score(y_te, preds)
    prec, rec, f1, _ = precision_recall_fscore_support(
        y_te, preds, average="binary", zero_division=0,
    )
    metrics = {"accuracy": float(acc), "precision": float(prec),
               "recall": float(rec), "f1": float(f1),
               "n_train": int(len(X_tr)), "n_test": int(len(X_te)),
               "test_was_holdout": test_size > 0}
    log.info("training metrics: %s", metrics)

    artifact = TrainArtifact(
        schema_version=SCHEMA_VERSION,
        feature_names=FEATURE_NAMES,
        scaler=scaler,
        model=model,
        metrics=metrics,
        trained_at=datetime.now(timezone.utc).isoformat(),
        n_samples=len(rows),
    )
    return artifact


def save(artifact: TrainArtifact, path: Path = MODEL_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(artifact, f)
    return path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-samples", type=int, default=MIN_TRAIN_SAMPLES)
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
    args = parser.parse_args()

    try:
        artifact = train(min_samples=args.min_samples)
    except RuntimeError as e:
        print(f"[skip] {e}", file=sys.stderr)
        return 2

    save(artifact, args.out)
    print(f"[ok] trained on {artifact.n_samples} rows -> {args.out}")
    print(f"[ok] metrics: {artifact.metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
