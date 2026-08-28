"""Photo producer contracts.

The inference worker produces these; `photo/evidence.py` converts them into the
engine's `photo` evidence family. Same split as the terrain package: all model
inference (impure, slow, GPU-optional) happens upstream, and everything that
decides what the evidence *means* is a pure function of these values.

Label keys and vocabularies match `ml/annotation/schema.py` exactly. That is
deliberate and load-bearing: the annotator's question, the model's output head,
and the engine's input share one vocabulary, so a human correction in the
adjudication ledger is directly reusable as a training target (docs §13.1 GT-4)
with no translation table to drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SceneScale = Literal["close_up", "mid", "landscape", "unknown"]

#: Decision after calibration and thresholding.
Decision = Literal["yes", "no", "abstain"]


@dataclass(frozen=True, slots=True)
class LabelPrediction:
    """One label's calibrated prediction.

    ``raw`` is kept alongside ``calibrated`` because a raw VLM similarity score
    is not a probability (docs §14.4) and the difference between the two is what
    the reliability diagram in the evaluation report is drawn from. Discarding
    the raw score would make the calibration unauditable.
    """

    key: str
    raw: float
    calibrated: float
    decision: Decision
    #: The calibrated-confidence band inside which the label abstains.
    abstain_band: tuple[float, float]

    def __post_init__(self) -> None:
        if not 0.0 <= self.calibrated <= 1.0:
            raise ValueError(f"{self.key}: calibrated {self.calibrated} outside [0,1]")
        low, high = self.abstain_band
        if not 0.0 <= low <= high <= 1.0:
            raise ValueError(f"{self.key}: invalid abstain band {self.abstain_band}")
        expected = (
            "abstain"
            if low <= self.calibrated <= high
            else ("yes" if self.calibrated > high else "no")
        )
        if self.decision != expected:
            raise ValueError(
                f"{self.key}: decision {self.decision!r} contradicts calibrated "
                f"{self.calibrated} against band {self.abstain_band} "
                f"(expected {expected!r}). The decision must be derivable from "
                "the calibrated score, or the verdict is not reproducible."
            )

    @property
    def abstained(self) -> bool:
        return self.decision == "abstain"


@dataclass(frozen=True, slots=True)
class PhotoLabels:
    """Everything the inference worker knows about one photograph."""

    image_id: str
    labels: dict[str, LabelPrediction]
    scene_scale: SceneScale
    #: Confidence in the scene_scale call itself. A model that cannot tell
    #: whether a frame is a close-up must not be allowed to unlock satellite
    #: cross-checking by guessing "mid".
    scene_scale_confidence: float = 1.0
    model_name: str = "unknown"
    model_version: str = "unknown"
    calibration_date: str | None = None
    #: Object key of the attention/CAM overlay, if produced.
    explanation_key: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, pred in self.labels.items():
            if key != pred.key:
                raise ValueError(f"label dict key {key!r} != prediction key {pred.key!r}")
        if not 0.0 <= self.scene_scale_confidence <= 1.0:
            raise ValueError(f"scene_scale_confidence {self.scene_scale_confidence} outside [0,1]")

    def get(self, key: str) -> LabelPrediction | None:
        return self.labels.get(key)

    def decided(self, key: str) -> bool:
        pred = self.labels.get(key)
        return pred is not None and not pred.abstained

    def says_yes(self, key: str) -> bool:
        pred = self.labels.get(key)
        return pred is not None and pred.decision == "yes"

    def says_no(self, key: str) -> bool:
        pred = self.labels.get(key)
        return pred is not None and pred.decision == "no"

    def abstained_keys(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, p in self.labels.items() if p.abstained))

    def lineage(self) -> dict[str, object]:
        return {
            "image_id": self.image_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "calibration_date": self.calibration_date,
            "scene_scale": self.scene_scale,
            "scene_scale_confidence": self.scene_scale_confidence,
            "abstained": list(self.abstained_keys()),
            "labels": {
                k: {"raw": p.raw, "calibrated": p.calibrated, "decision": p.decision}
                for k, p in self.labels.items()
            },
        }
