"""Confidence Net — tiny MLP that scores draft replies."""
from confidence.features import (
    FEATURE_NAMES,
    INTENT_LABELS,
    SCHEMA_VERSION,
    FeatureInputs,
    extract,
)
from confidence.labeling import collect_labeled_rows, to_matrices
from confidence.predict import ConfidenceScore, model_loaded, predict, reset
from confidence.train import save, train

__all__ = [
    "FEATURE_NAMES",
    "INTENT_LABELS",
    "SCHEMA_VERSION",
    "FeatureInputs",
    "extract",
    "collect_labeled_rows",
    "to_matrices",
    "ConfidenceScore",
    "model_loaded",
    "predict",
    "reset",
    "train",
    "save",
]
