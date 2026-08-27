"""Terrain plausibility rules (T10, docs §18.1).

> *"A rule you can print is worth more to an auditor than a model you can't."*
> — docs §14.2, task T8

This module is the deterministic backbone of the whole product. It is the family
that carries the `N3_TERRAIN_PATH` verdict, which means it is the only component
that can send a human being to inspect another human being's work. Three design
consequences follow, and all three are deliberate:

**1. Rules are data, not code.** `RULES` below is a table a hydrologist can
correct without touching a function. Every threshold declares its unit.

**2. Benefit of the doubt across the uncertainty disk.** A rule is only
`implausible` when the *most favourable* pixel in the location uncertainty disk
still violates it. If any pixel in the disk satisfies the rule, the claim is at
worst `marginal`. This costs recall and buys precision, which is the correct
trade when a false alarm damages a named officer's reputation — and it is why
the engine requires `terrain <= -1.0` (categorical) for `N3_TERRAIN_PATH`,
never a marginal reading.

**3. Every outcome carries a printable reason.** The reason string appears in
the UI, the API and the Evidence Pack verbatim. A rule that fires without
explaining itself is not shippable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.reconcile.signatures import signature_for
from app.services.reconcile.types import Plausibility
from app.services.terrain.types import (
    DiskStat,
    SlopeUnit,
    TerrainSample,
    percent_to_degrees,
)


@dataclass(frozen=True, slots=True)
class SlopeBand:
    """A slope constraint with an explicit unit.

    The unit is mandatory precisely because docs §18.1 states check dams in
    degrees and percolation tanks in percent. Storing a bare number here would
    reintroduce that ambiguity into the code.
    """

    minimum: float | None
    maximum: float | None
    unit: SlopeUnit

    def as_degrees(self) -> tuple[float | None, float | None]:
        if self.unit == "degrees":
            return self.minimum, self.maximum
        lo = None if self.minimum is None else percent_to_degrees(self.minimum)
        hi = None if self.maximum is None else percent_to_degrees(self.maximum)
        return lo, hi

    def describe(self) -> str:
        suffix = "deg" if self.unit == "degrees" else "%"
        if self.minimum is not None and self.maximum is not None:
            return f"slope {self.minimum:g}-{self.maximum:g}{suffix}"
        if self.maximum is not None:
            return f"slope < {self.maximum:g}{suffix}"
        if self.minimum is not None:
            return f"slope > {self.minimum:g}{suffix}"
        return "slope unconstrained"


@dataclass(frozen=True, slots=True)
class TerrainRule:
    """Machine-readable form of one row of the docs §18.1 rule table."""

    #: None means the rule does not constrain stream order.
    min_strahler: int | None = None
    max_strahler: int | None = None
    slope: SlopeBand | None = None
    #: Maximum distance to an extracted stream, metres.
    max_dist_to_stream_m: float | None = None
    #: Minimum distance to an extracted stream, metres. Used by types that must
    #: NOT sit in an active channel (plantation, contour bunds).
    min_dist_to_stream_m: float | None = None
    min_flow_accumulation_px: float | None = None
    max_flow_accumulation_px: float | None = None
    min_upstream_area_km2: float | None = None
    #: True when the type requires a depression (tank, pond, water body).
    requires_depression: bool = False
    # NOTE: there is deliberately no `forbid_in_channel` flag. "Must not be in
    # an active channel" is expressed operationally by `min_dist_to_stream_m`,
    # which is the constraint the DEM can actually measure. An earlier draft
    # carried both, and the boolean was never read by `evaluate()` — a config
    # field that looks protective and does nothing is worse than no field,
    # because someone will set it and believe it.
    #: Set when no terrain rule can be asserted for this type. The family is
    #: then reported unavailable rather than neutral — see the module docstring
    #: of `app.services.reconcile.types` on why that distinction matters.
    not_applicable: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


_NA = TerrainRule(not_applicable=True)

#: Keys match the PostgreSQL `intervention_type` enum and `SIGNATURES`.
RULES: dict[str, TerrainRule] = {
    "check_dam": TerrainRule(
        min_strahler=2,
        slope=SlopeBand(None, 5.0, "degrees"),
        max_dist_to_stream_m=30.0,
        min_flow_accumulation_px=100.0,
        notes=("A check dam impounds channel flow; off-channel siting cannot work.",),
    ),
    "percolation_tank": TerrainRule(
        min_strahler=2,
        slope=SlopeBand(None, 5.0, "percent"),
        requires_depression=True,
        max_dist_to_stream_m=150.0,
    ),
    "farm_pond": TerrainRule(
        slope=SlopeBand(None, 8.0, "percent"),
        # No stream-order requirement: a farm pond is fed by field runoff, not
        # by a channel. But it must receive runoff from *somewhere*, which is
        # why an implausibly low flow accumulation far from any drainage is the
        # signal that carries golden case 21.
        max_dist_to_stream_m=250.0,
        min_flow_accumulation_px=5.0,
    ),
    "nala_bund": TerrainRule(
        min_strahler=2,
        max_dist_to_stream_m=30.0,
        min_flow_accumulation_px=100.0,
    ),
    "earthen_bund": TerrainRule(
        slope=SlopeBand(1.0, 15.0, "percent"),
        min_dist_to_stream_m=20.0,
    ),
    "contour_bund": TerrainRule(
        slope=SlopeBand(1.0, 15.0, "percent"),
        min_dist_to_stream_m=20.0,
    ),
    "contour_trench": TerrainRule(
        slope=SlopeBand(5.0, 33.0, "percent"),
        # Upper catchment: small contributing area.
        max_flow_accumulation_px=5000.0,
    ),
    "staggered_trench": TerrainRule(
        slope=SlopeBand(5.0, 33.0, "percent"),
        max_flow_accumulation_px=5000.0,
    ),
    "gully_plug": TerrainRule(
        min_strahler=1,
        max_strahler=2,
        slope=SlopeBand(3.0, None, "percent"),
        max_dist_to_stream_m=50.0,
    ),
    "plantation": TerrainRule(
        min_dist_to_stream_m=10.0,
        notes=("Any slope is acceptable; only an active channel is excluded.",),
    ),
    "horticulture": TerrainRule(
        slope=SlopeBand(None, 20.0, "percent"),
        min_dist_to_stream_m=10.0,
    ),
    "waterbody_renovation": TerrainRule(
        requires_depression=True,
        slope=SlopeBand(None, 5.0, "percent"),
    ),
    # No terrain rule can be asserted for these. Reported unavailable, never
    # neutral, so coverage reflects that we genuinely could not assess siting.
    "recharge_shaft": _NA,
    "dug_well": _NA,
    "borewell": _NA,
    "livestock": _NA,
    "livelihood": _NA,
    "other": _NA,
}


@dataclass(frozen=True, slots=True)
class PlausibilityResult:
    """Outcome of the rule table for one claim."""

    verdict: Plausibility
    #: Signed agreement for the terrain evidence family, in [-1, 1].
    agreement: float
    available: bool
    #: Printed verbatim downstream.
    reason: str
    #: Individual constraint outcomes, for the evidence tree in the UI.
    checks: tuple[str, ...]
    rule_id: str

    def lineage(self) -> dict[str, object]:
        return {"rule_id": self.rule_id, "checks": list(self.checks)}


def _favourable_and_median(stat: DiskStat, *, prefer: str) -> tuple[float, float]:
    """Return (most favourable value in the disk, median).

    `prefer="high"` when a larger value satisfies the rule (stream order, flow
    accumulation, upstream area); `prefer="low"` when smaller does (slope,
    distance to stream).
    """
    return (stat.maximum if prefer == "high" else stat.minimum), stat.median


def evaluate(intervention_type: str, sample: TerrainSample) -> PlausibilityResult:
    """Apply the rule table. Pure; no IO.

    Returns `implausible` only when the most favourable pixel in the uncertainty
    disk still violates a constraint; `marginal` when the median violates but
    some pixel in the disk satisfies it.
    """
    try:
        rule = RULES[intervention_type]
    except KeyError:
        raise KeyError(
            f"no terrain rule for intervention_type {intervention_type!r}; "
            f"known types: {sorted(RULES)}"
        ) from None

    signature = signature_for(intervention_type)

    if rule.not_applicable:
        return PlausibilityResult(
            verdict="unknown",
            agreement=0.0,
            available=False,
            reason=(
                f"No terrain rule applies to intervention type "
                f"'{intervention_type}'. Siting cannot be assessed from a DEM for "
                f"this type, so the terrain family is reported unavailable rather "
                f"than neutral — coverage reflects that this was not assessed."
            ),
            checks=(),
            rule_id=f"{intervention_type}:not_applicable",
        )

    hard_violations: list[str] = []
    median_violations: list[str] = []
    satisfied: list[str] = []
    checks: list[str] = []

    def record(label: str, favourable_ok: bool, median_ok: bool, detail: str) -> None:
        checks.append(f"{label}: {detail}")
        if not favourable_ok:
            hard_violations.append(f"{label} ({detail})")
        elif not median_ok:
            median_violations.append(f"{label} ({detail})")
        else:
            satisfied.append(label)

    # --- Stream order -----------------------------------------------------
    if rule.min_strahler is not None:
        best, med = _favourable_and_median(sample.strahler_order, prefer="high")
        record(
            f"Strahler order >= {rule.min_strahler}",
            best >= rule.min_strahler,
            med >= rule.min_strahler,
            f"disk max {best:.0f}, median {med:.0f}",
        )
    if rule.max_strahler is not None:
        best, med = _favourable_and_median(sample.strahler_order, prefer="low")
        record(
            f"Strahler order <= {rule.max_strahler}",
            best <= rule.max_strahler,
            med <= rule.max_strahler,
            f"disk min {best:.0f}, median {med:.0f}",
        )

    # --- Slope ------------------------------------------------------------
    if rule.slope is not None:
        lo_deg, hi_deg = rule.slope.as_degrees()
        if hi_deg is not None:
            best, med = _favourable_and_median(sample.slope_deg, prefer="low")
            record(
                rule.slope.describe(),
                best <= hi_deg,
                med <= hi_deg,
                f"disk min {best:.2f} deg, median {med:.2f} deg, limit {hi_deg:.2f} deg",
            )
        if lo_deg is not None:
            best, med = _favourable_and_median(sample.slope_deg, prefer="high")
            record(
                rule.slope.describe() + " (lower bound)",
                best >= lo_deg,
                med >= lo_deg,
                f"disk max {best:.2f} deg, median {med:.2f} deg, minimum {lo_deg:.2f} deg",
            )

    # --- Distance to stream ------------------------------------------------
    if rule.max_dist_to_stream_m is not None:
        best, med = _favourable_and_median(sample.dist_to_stream_m, prefer="low")
        record(
            f"distance to stream <= {rule.max_dist_to_stream_m:.0f} m",
            best <= rule.max_dist_to_stream_m,
            med <= rule.max_dist_to_stream_m,
            f"disk min {best:.0f} m, median {med:.0f} m",
        )
    if rule.min_dist_to_stream_m is not None:
        best, med = _favourable_and_median(sample.dist_to_stream_m, prefer="high")
        record(
            f"distance to stream >= {rule.min_dist_to_stream_m:.0f} m (not in an active channel)",
            best >= rule.min_dist_to_stream_m,
            med >= rule.min_dist_to_stream_m,
            f"disk max {best:.0f} m, median {med:.0f} m",
        )

    # --- Flow accumulation -------------------------------------------------
    if rule.min_flow_accumulation_px is not None:
        best, med = _favourable_and_median(sample.flow_accumulation_px, prefer="high")
        record(
            f"flow accumulation >= {rule.min_flow_accumulation_px:.0f} px",
            best >= rule.min_flow_accumulation_px,
            med >= rule.min_flow_accumulation_px,
            f"disk max {best:.0f} px, median {med:.0f} px",
        )
    if rule.max_flow_accumulation_px is not None:
        best, med = _favourable_and_median(sample.flow_accumulation_px, prefer="low")
        record(
            f"flow accumulation <= {rule.max_flow_accumulation_px:.0f} px (upper catchment)",
            best <= rule.max_flow_accumulation_px,
            med <= rule.max_flow_accumulation_px,
            f"disk min {best:.0f} px, median {med:.0f} px",
        )

    # --- Upstream area -----------------------------------------------------
    if rule.min_upstream_area_km2 is not None:
        best, med = _favourable_and_median(sample.upstream_area_km2, prefer="high")
        record(
            f"upstream area >= {rule.min_upstream_area_km2:g} km2",
            best >= rule.min_upstream_area_km2,
            med >= rule.min_upstream_area_km2,
            f"disk max {best:.2f} km2, median {med:.2f} km2",
        )

    # --- Depression --------------------------------------------------------
    if rule.requires_depression:
        if sample.in_depression is None:
            checks.append("depression required: depression mask unavailable, not evaluated")
        else:
            record(
                "sited in a depression",
                sample.in_depression,
                sample.in_depression,
                "yes" if sample.in_depression else "no",
            )

    # --- Verdict -----------------------------------------------------------
    if hard_violations:
        # Categorical: even the most favourable pixel in the uncertainty disk
        # violates the rule. This is the only outcome that can carry
        # N3_TERRAIN_PATH, which is why it requires agreement of exactly -1.0.
        return PlausibilityResult(
            verdict="implausible",
            agreement=-1.0,
            available=True,
            reason=(
                f"Siting is implausible for a {intervention_type}: "
                f"{'; '.join(hard_violations)}. No part of the "
                f"{sample.disk_radius_m:.0f} m location uncertainty disk satisfies "
                f"these constraints. Expected siting: {signature.terrain_rule}."
            ),
            checks=tuple(checks),
            rule_id=f"{intervention_type}:implausible",
        )

    if median_violations:
        return PlausibilityResult(
            verdict="marginal",
            agreement=-0.4,
            available=True,
            reason=(
                f"Siting is marginal for a {intervention_type}: "
                f"{'; '.join(median_violations)}. Part of the "
                f"{sample.disk_radius_m:.0f} m location uncertainty disk does satisfy "
                f"these constraints, so this is not a categorical exclusion and "
                f"cannot on its own support a contradicted verdict."
            ),
            checks=tuple(checks),
            rule_id=f"{intervention_type}:marginal",
        )

    if not checks:
        return PlausibilityResult(
            verdict="unknown",
            agreement=0.0,
            available=False,
            reason=(
                f"No terrain constraint could be evaluated for this "
                f"{intervention_type} (required inputs unavailable)."
            ),
            checks=(),
            rule_id=f"{intervention_type}:no_checks_evaluated",
        )

    return PlausibilityResult(
        verdict="plausible",
        agreement=1.0,
        available=True,
        reason=(
            f"Siting is consistent with a {intervention_type}: "
            f"{', '.join(satisfied)} all satisfied across the "
            f"{sample.disk_radius_m:.0f} m location uncertainty disk. "
            f"Expected siting: {signature.terrain_rule}."
        ),
        checks=tuple(checks),
        rule_id=f"{intervention_type}:plausible",
    )
