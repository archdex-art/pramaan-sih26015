"""Evidence weights and engine thresholds — the single source of truth.

ADR-001 (docs §14.4): this file is the *only* place the weights exist. The API
serves them from here to the UI's "Method" panel, and
``scripts/render_worked_examples.py`` regenerates the design document's worked
examples from here. There is therefore no way for the document and the code to
disagree about what the system does — which is the failure that produced the
unreproducible confidence figure in an earlier draft.

The weights are a documented ASSUMPTION, not a fitted parameter. They stay one
until the adjudication ledger holds enough decisions to fit them, at which point
they become an estimate with a confidence interval and this docstring changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.reconcile.types import FAMILIES, Family

#: Frozen family weights (ADR-001). The sum is exactly 1.0, asserted at import
#: time below so a careless edit fails fast at startup rather than silently
#: rescaling every verdict in the system.
#:
#: Ordering is strict and each step is defensible in one sentence:
#:   terrain   — fully independent AND the only family unaffected by cloud,
#:               sensor resolution or season. Heaviest for that reason.
#:   satellite — fully independent, but bounded by the 30 m detection limit.
#:   temporal  — fully independent, but needs usable scenes in both windows.
#:   control   — fully independent and the strongest design element we have,
#:               weighted below satellite/temporal only because a thin matched
#:               pool makes it unavailable more often than they are.
#:   photo     — the photograph IS the claim's own source, so it must never
#:               outvote independent evidence. Lowest substantive weight.
#:   context   — a confounder check (rainfall), not primary evidence.
#:
#: Independent families total 0.88 against the photo family's 0.12: independent
#: evidence outweighs self-report roughly 7:1. That ratio is the design claim.
DEFAULT_WEIGHTS: dict[Family, float] = {
    "terrain": 0.25,
    "satellite": 0.20,
    "temporal": 0.20,
    "control": 0.15,
    "photo": 0.12,
    "context": 0.08,
}


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Every tunable the engine has. Hashable, so it goes into the lineage record.

    Nothing in the engine reads a module-level constant that is not reachable
    from here — that is what makes a verdict reproducible from its lineage.
    """

    weights: dict[Family, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # --- Agreement interpretation -----------------------------------------
    #: |agreement| below this is treated as neutral, not as support. Keeps a
    #: 0.02 wobble from being reported as "this family agrees".
    agreement_epsilon: float = 0.05
    #: A family "agrees with the claim" only above this. Deliberately well
    #: clear of noise: three families at 0.3 must not add up to L3.
    agreeing_threshold: float = 0.35
    #: A family is "clearly inconsistent" at or below this. Symmetric.
    disagreeing_threshold: float = -0.35

    # --- Level thresholds (docs §16.2 STEP 10) ----------------------------
    l4_min_agreeing_families: int = 3
    l4_min_coverage: float = 0.70
    l3_min_agreeing_families: int = 3
    l2_min_agreeing_families: int = 2

    # --- N3 gates ---------------------------------------------------------
    #: Both N3 paths refuse to fire below this data sufficiency. Absence of
    #: evidence is not evidence of absence (docs §16.4).
    n3_min_data_sufficiency: float = 0.35
    #: N3_SATELLITE_PATH needs the expected signature absent in at least this
    #: many *independent* families.
    n3_satellite_min_disagreeing: int = 2
    #: N3_TERRAIN_PATH needs terrain at or below this. -1.0 means only a
    #: categorically implausible terrain rule qualifies, never a marginal one.
    n3_terrain_max_agreement: float = -1.0

    # --- N2 ---------------------------------------------------------------
    #: Unsupported: signature absent, but explanations remain / gates not met.
    n2_max_score: float = -0.15

    # --- Verdict labels ---------------------------------------------------
    corroborated_min_score: float = 0.35
    partial_min_score: float = 0.15

    # --- Priority ---------------------------------------------------------
    #: Priority 1 requires a deterministic (non-AI) driver: terrain. An
    #: AI-driven flag never reaches the top of a human's work queue on its own.
    priority_1_requires_terrain_driver: bool = True

    def weight(self, family: Family) -> float:
        return self.weights[family]

    def weight_sum(self) -> float:
        return sum(self.weights.values())

    def fingerprint(self) -> str:
        """Stable digest of the config, for the lineage record.

        Deliberately hand-rolled from sorted items rather than hashing a dict
        repr: dict ordering and float repr are both things we do not want a
        verdict's identity to depend on.
        """
        parts = [f"{k}={self.weights[k]:.6f}" for k in sorted(self.weights)]
        parts += [
            f"agreeing={self.agreeing_threshold:.6f}",
            f"disagreeing={self.disagreeing_threshold:.6f}",
            f"eps={self.agreement_epsilon:.6f}",
            f"l4cov={self.l4_min_coverage:.6f}",
            f"l4fam={self.l4_min_agreeing_families}",
            f"l3fam={self.l3_min_agreeing_families}",
            f"l2fam={self.l2_min_agreeing_families}",
            f"n3suff={self.n3_min_data_sufficiency:.6f}",
            f"n3sat={self.n3_satellite_min_disagreeing}",
            f"n3ter={self.n3_terrain_max_agreement:.6f}",
            f"n2max={self.n2_max_score:.6f}",
            f"corrmin={self.corroborated_min_score:.6f}",
            f"partmin={self.partial_min_score:.6f}",
        ]
        return "|".join(parts)


def _validate_weights(weights: dict[Family, float]) -> None:
    missing = set(FAMILIES) - set(weights)
    extra = set(weights) - set(FAMILIES)
    if missing or extra:
        raise ValueError(
            f"weights must cover exactly the six frozen families; "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"weights must sum to 1.0 (they define coverage); got {total!r}. "
            "If you are intentionally reweighting, keep the sum at 1.0 so "
            "coverage remains interpretable as a fraction."
        )
    negative = {k: v for k, v in weights.items() if v < 0}
    if negative:
        raise ValueError(f"negative weights make agreement non-monotonic: {negative}")


_validate_weights(DEFAULT_WEIGHTS)

#: Bumped whenever engine behaviour changes. Stamped on every verdict; a bump
#: re-runs the golden suite and regenerates the design doc's worked examples.
ENGINE_VERSION = "engine-v1"
