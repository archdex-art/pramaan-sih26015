"""Expected-signature table per intervention type (docs §18.1).

This module is the *domain knowledge* of the system: it is what turns generic
change detection into watershed intelligence. It is deliberately data, not code
— a table an auditor can read and a hydrologist can correct without touching
the engine.

Two columns matter more than the rest:

``ceiling``
    The highest epistemic level this intervention type can ever reach, no matter
    how much evidence agrees. A dug well has no optical signature, so no volume
    of satellite corroboration can push it past "we can see it exists". The
    engine clamps to this, and the clamp is recorded in ``rule_path``.

``typical_footprint_m2``
    Feeds the detectability gate. Sourced from the ranges in docs §18.1; where
    the doc gives a range we store both bounds and use the midpoint, because a
    single number here would be a false precision the gate then inherits.

The last two rows (dug_well, livestock/livelihood) are strategically vital:
publishing the list of things the system *cannot* assess is what makes the rest
of the table believable (docs §18.1 closing note).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile.types import Level

#: Indices whose *increase* corroborates a claim, per type.
Direction = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Signature:
    """What we expect to see if this intervention was built and is working."""

    type_key: str
    purpose: str
    #: Indices expected to rise if the claim is true.
    expect_increase: Direction
    #: Indices expected to fall if the claim is true.
    expect_decrease: Direction
    #: Where the satellite AOI is drawn.
    aoi: str
    #: Footprint range from docs §18.1; midpoint drives the gate.
    footprint_min_m2: float
    footprint_max_m2: float
    #: Plain-language terrain rule, printed verbatim in the Evidence Pack.
    terrain_rule: str
    #: Hard ceiling on the epistemic level for this type.
    ceiling: Level
    #: True when the type has no reliable optical signature at 30 m. The engine
    #: refuses to score the satellite/temporal/control families as *disagreeing*
    #: for these types — absence of a signature we never expected to see is not
    #: evidence against the claim.
    optically_assessable: bool = True
    note: str = ""

    @property
    def typical_footprint_m2(self) -> float:
        return (self.footprint_min_m2 + self.footprint_max_m2) / 2.0


# NOTE: keys match the PostgreSQL `intervention_type` enum in migration 0001.
SIGNATURES: dict[str, Signature] = {
    "check_dam": Signature(
        type_key="check_dam",
        purpose="Impound runoff, recharge, extend water availability",
        expect_increase=("MNDWI", "water_persistence_months", "NDVI_rabi_command"),
        expect_decrease=(),
        aoi="site disk + 300 m command buffer",
        footprint_min_m2=1_000.0,
        footprint_max_m2=10_000.0,
        terrain_rule="Strahler order >= 2; flow accumulation above the calibrated "
        "threshold; slope < 5 deg; distance-to-stream < 30 m",
        ceiling=Level.L4_CONTROL_DIFFERENCED,
    ),
    "percolation_tank": Signature(
        type_key="percolation_tank",
        purpose="Recharge groundwater",
        expect_increase=("MNDWI", "NDVI_rabi_buffer", "NDVI_summer_buffer"),
        expect_decrease=(),
        aoi="tank + 500-1000 m recharge buffer",
        footprint_min_m2=2_000.0,
        footprint_max_m2=20_000.0,
        terrain_rule="Strahler order >= 2; in a valley or depression; slope < 5%",
        ceiling=Level.L3_MULTI_INDICATOR,
        note="Recharge cannot be observed optically. The tank is observable; the "
        "groundwater effect is inferred indirectly and therefore capped at L3.",
    ),
    "farm_pond": Signature(
        type_key="farm_pond",
        purpose="On-farm storage for protective irrigation",
        expect_increase=("MNDWI", "NDVI_rabi_parcel"),
        expect_decrease=(),
        aoi="pond + 100 m",
        footprint_min_m2=400.0,
        footprint_max_m2=2_500.0,
        terrain_rule="Depression or low slope; near cropland; slope < 8%",
        ceiling=Level.L3_MULTI_INDICATOR,
        note="Typically below the 30 m detection limit — usually cluster-assessed.",
    ),
    "nala_bund": Signature(
        type_key="nala_bund",
        purpose="Channel stabilisation and storage",
        expect_increase=("MNDWI", "water_persistence_months"),
        expect_decrease=(),
        aoi="channel reach buffer",
        footprint_min_m2=800.0,
        footprint_max_m2=8_000.0,
        terrain_rule="Strahler order >= 2; along the channel",
        ceiling=Level.L3_MULTI_INDICATOR,
    ),
    "earthen_bund": Signature(
        type_key="earthen_bund",
        purpose="Reduce runoff velocity, retain moisture",
        expect_increase=("NDVI", "NDMI"),
        expect_decrease=("BSI",),
        aoi="treated parcel polygon",
        footprint_min_m2=5_000.0,
        footprint_max_m2=40_000.0,
        terrain_rule="Slope 1-15%; on cropland or wasteland; not in a channel",
        ceiling=Level.L3_MULTI_INDICATOR,
        note="The individual bund is not detectable; the treated area is.",
    ),
    "contour_bund": Signature(
        type_key="contour_bund",
        purpose="Reduce runoff velocity, retain moisture",
        expect_increase=("NDVI", "NDMI"),
        expect_decrease=("BSI",),
        aoi="treated parcel polygon",
        footprint_min_m2=5_000.0,
        footprint_max_m2=40_000.0,
        terrain_rule="Slope 1-15%; on cropland or wasteland; not in a channel",
        ceiling=Level.L3_MULTI_INDICATOR,
    ),
    "contour_trench": Signature(
        type_key="contour_trench",
        purpose="Moisture conservation on slopes",
        expect_increase=("NDVI", "NDMI"),
        expect_decrease=("BSI",),
        aoi="treated block polygon (not a point)",
        footprint_min_m2=1.0,
        footprint_max_m2=3.0,
        terrain_rule="Slope 5-33%; upper catchment",
        ceiling=Level.L3_MULTI_INDICATOR,
        note="An individual trench is ~1 m wide — far below the detection limit. "
        "Block-level assessment requires a treated-area polygon of >= ~2 ha.",
    ),
    "staggered_trench": Signature(
        type_key="staggered_trench",
        purpose="Moisture conservation on slopes",
        expect_increase=("NDVI", "NDMI"),
        expect_decrease=("BSI",),
        aoi="treated block polygon (not a point)",
        footprint_min_m2=1.0,
        footprint_max_m2=3.0,
        terrain_rule="Slope 5-33%; upper catchment",
        ceiling=Level.L3_MULTI_INDICATOR,
    ),
    "gully_plug": Signature(
        type_key="gully_plug",
        purpose="Arrest gully erosion",
        expect_increase=("NDVI",),
        expect_decrease=("BSI",),
        aoi="gully corridor",
        footprint_min_m2=20.0,
        footprint_max_m2=200.0,
        terrain_rule="Strahler order 1-2; high local slope; near a drainage line",
        ceiling=Level.L2_CORROBORATED,
        note="Rarely detectable at 30 m. Escalate to cluster assessment.",
    ),
    "plantation": Signature(
        type_key="plantation",
        purpose="Vegetative cover, soil binding",
        expect_increase=("NDVI",),
        expect_decrease=("BSI",),
        aoi="plantation polygon",
        footprint_min_m2=10_000.0,
        footprint_max_m2=100_000.0,
        terrain_rule="Any slope; must not be in an active channel",
        ceiling=Level.L4_CONTROL_DIFFERENCED,
        note="The most satellite-detectable intervention of all: a sustained, "
        "multi-year NDVI rise over a delineated block.",
    ),
    "horticulture": Signature(
        type_key="horticulture",
        purpose="Livelihood plus vegetative cover",
        expect_increase=("NDVI",),
        expect_decrease=(),
        aoi="parcel polygon",
        footprint_min_m2=5_000.0,
        footprint_max_m2=50_000.0,
        terrain_rule="Cropland or wasteland",
        ceiling=Level.L4_CONTROL_DIFFERENCED,
    ),
    "waterbody_renovation": Signature(
        type_key="waterbody_renovation",
        purpose="Restore storage capacity of an existing water body",
        expect_increase=("water_persistence_months", "max_water_extent", "MNDWI"),
        expect_decrease=(),
        aoi="water body polygon",
        footprint_min_m2=5_000.0,
        footprint_max_m2=100_000.0,
        terrain_rule="In a depression with an existing water history",
        ceiling=Level.L4_CONTROL_DIFFERENCED,
        note="Best-evidenced category: JRC Global Surface Water supplies a free "
        "multi-decadal pre-intervention baseline.",
    ),
    "recharge_shaft": Signature(
        type_key="recharge_shaft",
        purpose="Direct groundwater recharge",
        expect_increase=(),
        expect_decrease=(),
        aoi="not applicable",
        footprint_min_m2=4.0,
        footprint_max_m2=25.0,
        terrain_rule="In or adjacent to a drainage line; permeable strata",
        ceiling=Level.L1_OBSERVED,
        optically_assessable=False,
        note="No reliable optical signature. The system reports existence only "
        "and refuses outcome claims.",
    ),
    "dug_well": Signature(
        type_key="dug_well",
        purpose="Groundwater extraction",
        expect_increase=(),
        expect_decrease=(),
        aoi="not applicable",
        footprint_min_m2=4.0,
        footprint_max_m2=80.0,
        terrain_rule="not applicable",
        ceiling=Level.L1_OBSERVED,
        optically_assessable=False,
        note="No reliable optical signature. Existence only; the system refuses "
        "outcome claims for this type.",
    ),
    "borewell": Signature(
        type_key="borewell",
        purpose="Groundwater extraction",
        expect_increase=(),
        expect_decrease=(),
        aoi="not applicable",
        footprint_min_m2=0.1,
        footprint_max_m2=1.0,
        terrain_rule="not applicable",
        ceiling=Level.L1_OBSERVED,
        optically_assessable=False,
        note="No reliable optical signature. Existence only.",
    ),
    "livestock": Signature(
        type_key="livestock",
        purpose="Socio-economic livelihood support",
        expect_increase=(),
        expect_decrease=(),
        aoi="not applicable",
        footprint_min_m2=0.0,
        footprint_max_m2=0.0,
        terrain_rule="not applicable",
        ceiling=Level.L0_RECORDED,
        optically_assessable=False,
        note="No satellite signature. Explicitly out of scope for satellite "
        "reconciliation — stated, not silently mishandled.",
    ),
    "livelihood": Signature(
        type_key="livelihood",
        purpose="Socio-economic livelihood support",
        expect_increase=(),
        expect_decrease=(),
        aoi="not applicable",
        footprint_min_m2=0.0,
        footprint_max_m2=0.0,
        terrain_rule="not applicable",
        ceiling=Level.L0_RECORDED,
        optically_assessable=False,
        note="No satellite signature. Explicitly out of scope.",
    ),
    "other": Signature(
        type_key="other",
        purpose="Unclassified work",
        expect_increase=(),
        expect_decrease=(),
        aoi="site disk",
        footprint_min_m2=100.0,
        footprint_max_m2=1_000.0,
        terrain_rule="not applicable — type unknown, no rule can be asserted",
        ceiling=Level.L1_OBSERVED,
        optically_assessable=False,
        note="An unclassified work has no expected signature, so nothing can "
        "corroborate or contradict it beyond its own existence.",
    ),
}


def signature_for(intervention_type: str) -> Signature:
    """Look up a signature, failing loudly on an unknown type.

    A silent fallback to a generic signature would let a typo produce a
    confident verdict against the wrong expectations — the single most dangerous
    failure this table can have.
    """
    try:
        return SIGNATURES[intervention_type]
    except KeyError:
        raise KeyError(
            f"no expected signature for intervention_type {intervention_type!r}; "
            f"known types: {sorted(SIGNATURES)}"
        ) from None
