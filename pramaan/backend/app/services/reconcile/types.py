"""Frozen contracts for the reconciliation engine.

This module is the parallelisation boundary of the whole project (docs §28.4):
every producer — ingestion, terrain, satellite, temporal, controls, photo AI —
targets these types, so producers can be built independently while the engine
is already tested against synthetic bundles.

Design constraints, all deliberate:

* Everything is a frozen dataclass with ``slots=True``. Verdicts are government
  evidence; an evidence bundle that can be mutated after a verdict is computed
  destroys the reproducibility guarantee in docs §21.3.
* No imports beyond ``dataclasses``/``enum``/``typing``. The purity test in
  ``tests/unit/test_engine_purity.py`` asserts this over the AST — no IO, no
  clock, no RNG anywhere in this package.
* No ``datetime`` fields. The engine is a pure function of evidence; the
  *timestamp* of a computation is the caller's concern and lives in the DB row,
  never in the engine's input, because a timestamp in the input would make
  verdicts irreproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Evidence families — frozen by ADR-001 (docs §14.4 / §16.1).
# `metadata` is deliberately NOT a family: it is a trust multiplier that enters
# through Quality.metadata_integrity, and counting it twice would penalise a bad
# geotag in both the numerator and the multiplier.
# ---------------------------------------------------------------------------
Family = Literal["terrain", "satellite", "temporal", "photo", "control", "context"]

FAMILIES: tuple[Family, ...] = (
    "terrain",
    "satellite",
    "temporal",
    "photo",
    "control",
    "context",
)

#: Families whose evidence is independent of the claim itself. `photo` is
#: excluded: the photograph *is* the claim's source, so it cannot corroborate
#: itself. Used by the L2 rule ("at least one non-photo family").
INDEPENDENT_FAMILIES: frozenset[Family] = frozenset(
    {"terrain", "satellite", "temporal", "control", "context"}
)


class Level(StrEnum):
    """The epistemic ladder (docs §7.3). Values match the PostgreSQL
    ``epistemic_level`` enum exactly — the DB and the engine share one vocabulary.

    L5 (causal) is deliberately absent from this enum, not merely unreachable:
    an enum member that code can never construct is an invitation for someone to
    construct it. PRAMAAN's ceiling is L4 and the type system says so.
    """

    L0_RECORDED = "L0_recorded"
    L1_OBSERVED = "L1_observed"
    L2_CORROBORATED = "L2_corroborated"
    L3_MULTI_INDICATOR = "L3_multi_indicator"
    L4_CONTROL_DIFFERENCED = "L4_control_differenced"
    N1_INCONCLUSIVE = "N1_inconclusive"
    N2_UNSUPPORTED = "N2_unsupported"
    N3_CONTRADICTED = "N3_contradicted"


#: Human-facing verdict labels. Distinct from Level: Level says *how strongly
#: known*, Label says *what*. The UI shows Level first (docs §24.4).
Label = Literal["CORROBORATED", "PARTIAL", "INCONCLUSIVE", "UNSUPPORTED", "CONTRADICTED"]

#: Recommended actions (docs §16.2 STEP 12). Vocabulary-locked: the strongest
#: phrase in the system is "requires physical verification" (W6 fix, docs §37).
Action = Literal[
    "no_action",
    "confirm_next_cycle",
    "physical_verification",
    "data_quality_fix",
    "recapture_geotag",
]

SceneScale = Literal["close_up", "mid", "landscape", "unknown"]

#: Terrain plausibility outcome (docs §18 rule table).
Plausibility = Literal["plausible", "marginal", "implausible", "unknown"]


@dataclass(frozen=True, slots=True)
class FamilyEvidence:
    """One family's signed verdict on whether reality matches the claim.

    ``agreement`` is scored against the *expected signature* for the
    intervention type (docs §18.1), never in the abstract:
      +1 fully consistent · 0 neutral · -1 clearly inconsistent.

    ``available`` is the ``a_e`` term. An unavailable family contributes nothing
    to ``support`` **and** nothing to ``weight_total``, so missing data lowers
    coverage rather than silently reading as neutral agreement. That distinction
    is the whole reason coverage exists.
    """

    family: Family
    agreement: float
    available: bool
    reason: str
    #: Scene ids, DEM version, model tag, control ids — whatever this family
    #: used. Copied verbatim into the verdict's lineage (docs §21.3).
    lineage: dict[str, object] = field(default_factory=dict)
    #: True when this family was computed at cluster scale after the
    #: detectability gate failed (docs §16.2 STEP 3). Recorded because a
    #: cluster-scale observation must never be presented as a per-structure one.
    cluster_scale: bool = False

    def __post_init__(self) -> None:
        if not -1.0 <= self.agreement <= 1.0:
            raise ValueError(
                f"{self.family}: agreement {self.agreement} outside [-1, 1] — "
                "producers must normalise before handing evidence to the engine"
            )
        if not self.reason:
            raise ValueError(
                f"{self.family}: empty reason. Every family must state why it "
                "scored as it did; the reason string is printed in the Evidence Pack."
            )


@dataclass(frozen=True, slots=True)
class Gates:
    """Pre-evidence gates that constrain what the engine is allowed to conclude.

    The detectability gate (docs §16.2 STEP 3) runs *before* any satellite
    evidence is computed, because "we looked and saw nothing" is meaningless when
    the structure is smaller than a pixel.
    """

    detectability_passed: bool
    expected_footprint_m2: float
    pixel_area_m2: float
    escalated_to_cluster: bool
    scene_scale: SceneScale = "unknown"
    terrain_plausibility: Plausibility = "unknown"

    @property
    def footprint_pixels(self) -> float:
        if self.pixel_area_m2 <= 0:
            raise ValueError("pixel_area_m2 must be positive")
        return self.expected_footprint_m2 / self.pixel_area_m2


@dataclass(frozen=True, slots=True)
class Quality:
    """The two multipliers that scale confidence but never move the score.

    Keeping these out of ``support`` is ADR-001's key structural decision: bad
    metadata makes us *less sure* of a verdict, it does not make the underlying
    evidence disagree.
    """

    #: GPS accuracy, coordinate provenance rank, timestamp/EXIF consistency.
    metadata_integrity: float
    #: Usable scenes per window per season, cloud-masked fraction, control count.
    data_sufficiency: float

    def __post_init__(self) -> None:
        for name in ("metadata_integrity", "data_sufficiency"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} {value} outside [0, 1]")

    @property
    def product(self) -> float:
        return self.metadata_integrity * self.data_sufficiency


@dataclass(frozen=True, slots=True)
class Alternative:
    """A competing explanation for the observed evidence.

    Tracked explicitly because both N3 paths require at least one alternative to
    have been *actively excluded* (docs §16.2 STEP 10). "We didn't think of any"
    must not read the same as "we ruled them out".
    """

    description: str
    excluded: bool
    #: Why it was excluded, or why it could not be. Printed in the dissent panel.
    basis: str


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Everything the engine is allowed to see. No IO handles, no DB session."""

    claim_id: str
    intervention_type: str
    families: tuple[FamilyEvidence, ...]
    gates: Gates
    quality: Quality
    alternatives: tuple[Alternative, ...] = ()
    #: Data-limitation notes surfaced by producers (cloud gaps, short series).
    #: They land in the dissent panel verbatim.
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        seen = [f.family for f in self.families]
        duplicates = {name for name in seen if seen.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate families in bundle: {sorted(duplicates)}")
        unknown = set(seen) - set(FAMILIES)
        if unknown:
            raise ValueError(
                f"unknown families {sorted(unknown)}; the family set is frozen "
                f"by ADR-001 to {list(FAMILIES)}"
            )

    def by_family(self) -> dict[Family, FamilyEvidence]:
        return {f.family: f for f in self.families}

    def get(self, family: Family) -> FamilyEvidence | None:
        for candidate in self.families:
            if candidate.family == family:
                return candidate
        return None

    def available(self) -> tuple[FamilyEvidence, ...]:
        return tuple(f for f in self.families if f.available)

    def excluded_alternatives(self) -> tuple[Alternative, ...]:
        return tuple(a for a in self.alternatives if a.excluded)


@dataclass(frozen=True, slots=True)
class Aggregate:
    """Intermediate arithmetic, kept as a value so it can be asserted on."""

    support: float
    weight_total: float
    score: float
    coverage: float
    quality: float
    confidence: float


@dataclass(frozen=True, slots=True)
class Verdict:
    """The engine's output. Reproducible byte-for-byte from a lineage record."""

    claim_id: str
    label: Label
    level: Level
    score: float
    confidence: float
    coverage: float
    quality: float
    data_sufficiency: float
    #: Named rules that fired, in order. This is what makes a verdict diffable:
    #: when a golden case regresses you see *which rule* changed, not just that
    #: the label moved. Also what distinguishes N3_TERRAIN_PATH from
    #: N3_SATELLITE_PATH in the UI and in the PDF.
    rule_path: tuple[str, ...]
    dissent: tuple[str, ...]
    recommended_action: Action
    #: 1 (most urgent) .. 5. Only meaningful when action is physical_verification.
    priority: int | None
    engine_version: str
    weights: dict[str, float]
    lineage: dict[str, object]

    def __post_init__(self) -> None:
        # Invariant I5 (docs §14.4): a verdict without stated counter-evidence
        # is not shippable. Enforced at construction so no code path can emit one.
        if not self.dissent:
            raise ValueError(
                f"claim {self.claim_id}: empty dissent panel. Every verdict must "
                "state what points the other way (docs §16.2 STEP 11)."
            )
        # Invariant I1: confidence = |score| * coverage * quality, and both
        # multipliers are in [0, 1]. This is the exact defect that produced the
        # unreproducible confidence 0.71 in an earlier draft of Worked Example B.
        if self.confidence > abs(self.score) + 1e-9:
            raise ValueError(
                f"claim {self.claim_id}: confidence {self.confidence} exceeds "
                f"|score| {abs(self.score)} — arithmetically impossible"
            )
