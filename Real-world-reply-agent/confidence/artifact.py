"""Trained Confidence-Net artifact container.

Kept in its OWN module (not train.py) on purpose: pickle records a class by its
module path. If TrainArtifact lived in train.py and training ran via
`python -m confidence.train`, the class would be pickled as `__main__.TrainArtifact`
and fail to unpickle in the poller / dashboard / predict process (whose __main__
is something else). Defining it here means the path is always
`confidence.artifact.TrainArtifact`, so the model loads everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler


@dataclass
class TrainArtifact:
    schema_version: int
    feature_names: tuple[str, ...]
    scaler: StandardScaler
    model: MLPClassifier
    metrics: dict
    trained_at: str
    n_samples: int
