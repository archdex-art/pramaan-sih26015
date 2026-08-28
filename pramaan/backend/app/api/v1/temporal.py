"""Temporal comparison endpoint — the data behind the S7 hero chart.

docs §24 S7 is the screen the pitch leads with: the site's index line against a
shaded control ribbon, with the construction band excluded. This endpoint serves
exactly what that chart draws and nothing it does not.

## It reads, it does not recompute

Every number here was produced by the temporal producer and stored in
`evidence.lineage` at reconciliation time. Recomputing on read would let the
chart disagree with the verdict it sits beside — the chart would show one delta
and the verdict card another, both correct for different inputs. So this is a
projection of stored evidence, and if the evidence is absent the endpoint says
so rather than deriving a replacement.

## The caveat is part of the payload

`control_basis` travels with the ribbon. When controls were not covariate-matched
— currently the case until DEM derivatives land — the chart must say so on the
chart, not in a tooltip and not only in the lineage. A shaded band that looks
like matched controls but is not would claim more than the data supports, and
that is the one failure this product cannot afford.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import db_session

router = APIRouter(tags=["temporal"])

DbSession = Annotated[Session, Depends(db_session)]

_SELECT_EVIDENCE = text("""
SELECT family, agreement, available, payload, lineage
FROM evidence WHERE claim_id = :claim_id
""")

_SELECT_CLAIM = text("""
SELECT c.id, c.asserted_date, c.detectability, i.unique_id, i.type
FROM claims c JOIN interventions i ON i.id = c.intervention_id
WHERE c.id = :claim_id
""")


class SeasonPoint(BaseModel):
    """One season's observation. `site` is null when nothing was usable."""

    year: int
    season: str
    site: float | None
    controls: list[float | None]
    usable_fraction: float
    n_scenes: int
    scene_ids: list[str] = Field(
        default_factory=list,
        description="Granule ids behind this composite; the chart shows them on hover.",
    )


class ControlBand(BaseModel):
    """The shaded ribbon, per season pairing."""

    season: str
    pre_year: int
    post_year: int
    site_delta: float
    control_median_delta: float
    control_p10: float
    control_p90: float
    differenced_estimate: float
    site_inside_control_band: bool
    n_controls: int


class TrendOut(BaseModel):
    direction: str
    insufficient: bool
    n: int
    slope_per_year: float | None = None
    p_value: float | None = None
    min_points_required: int | None = None


class TemporalOut(BaseModel):
    claim_id: int
    intervention_unique_id: str
    intervention_type: str
    index: str
    claimed_date: str

    #: PRE/POST windows and the excluded construction band, from the producer.
    windows: dict[str, Any]
    series: list[SeasonPoint]
    bands: list[ControlBand]
    trend: TrendOut | None

    #: Seasons the producer refused to assess, and why. Rendered as a gap with
    #: a reason, never as a missing point the eye interpolates across.
    excluded_seasons: dict[str, str] = Field(default_factory=dict)

    control_available: bool = Field(
        description="False when controls were not covariate-matched. The chart "
        "must label the ribbon accordingly."
    )
    control_basis: str = Field(
        description="How the ribbon was constructed. Displayed on the chart."
    )
    temporal_agreement: float
    n_scenes_total: int
    provenance: str


def _band(raw: dict[str, Any]) -> ControlBand:
    return ControlBand(
        season=str(raw["season"]),
        pre_year=int(raw["pre_year"]),
        post_year=int(raw["post_year"]),
        site_delta=float(raw["site_delta"]),
        control_median_delta=float(raw["control_median_delta"]),
        control_p10=float(raw["control_p10"]),
        control_p90=float(raw["control_p90"]),
        differenced_estimate=float(raw["differenced_estimate"]),
        site_inside_control_band=bool(raw["site_inside_control_band"]),
        n_controls=int(raw["n_controls"]),
    )


@router.get("/claims/{claim_id}/temporal", response_model=TemporalOut)
def get_temporal(claim_id: int, session: DbSession) -> TemporalOut:
    claim = session.execute(_SELECT_CLAIM, {"claim_id": claim_id}).first()
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"claim {claim_id} does not exist")

    rows = {
        row["family"]: row
        for row in session.execute(_SELECT_EVIDENCE, {"claim_id": claim_id}).mappings()
    }
    temporal = rows.get("temporal")
    if temporal is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"claim {claim_id} has no temporal evidence. Reconciliation has not "
            "run, or ran before the temporal producer was available.",
        )

    lineage: dict[str, Any] = dict(temporal["lineage"] or {})
    observed = lineage.get("observed_series")
    if not observed:
        # 422, not an empty chart: an empty ribbon over an empty axis reads as
        # "nothing happened", which is a different claim from "not recorded".
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"claim {claim_id} has temporal evidence but no observed series in "
            "its lineage, so the chart cannot be drawn from stored evidence.",
        )

    control = rows.get("control")
    control_lineage: dict[str, Any] = dict(control["lineage"] or {}) if control else {}

    # Per-season comparisons live under `per_season` once the control producer
    # has run. The two older keys are read as fallbacks so a claim reconciled
    # before matched controls existed still draws its ribbon — renaming the key
    # silently emptied `bands` and the chart lost its ribbon entirely.
    bands_raw = (
        control_lineage.get("per_season")
        or control_lineage.get("preliminary_ring_observation")
        or lineage.get("pairings")
        or []
    )

    control_available = bool(control and control["available"])
    if control_available:
        # The ControlSet's own reason, not its criteria dict: this string is
        # rendered on the chart, and a dict of thresholds is not a sentence.
        payload_reason = (control["payload"] or {}).get("reason") if control else None
        control_basis = str(
            payload_reason or control_lineage.get("reason") or "covariate-matched controls"
        )
    else:
        control_basis = str(
            (bands_raw[0] if bands_raw else {}).get("control_basis", "controls unavailable")
        )

    trend_raw = (lineage.get("trends") or {}).get(str(lineage.get("index", "NDVI")))
    trend = (
        TrendOut(
            direction=str(trend_raw["direction"]),
            insufficient=bool(trend_raw["insufficient"]),
            n=int(trend_raw["n"]),
            slope_per_year=trend_raw.get("slope_per_year"),
            p_value=trend_raw.get("p_value"),
            min_points_required=trend_raw.get("min_points_required"),
        )
        if trend_raw
        else None
    )

    excluded: dict[str, str] = {}
    if lineage.get("kharif"):
        excluded["kharif"] = str(lineage["kharif"])

    return TemporalOut(
        claim_id=claim_id,
        intervention_unique_id=str(claim[3]),
        intervention_type=str(claim[4]),
        index=str(lineage.get("index", "NDVI")),
        claimed_date=str(claim[1]),
        windows=dict(lineage.get("windows") or {}),
        series=[SeasonPoint(**point) for point in observed],
        bands=[_band(b) for b in bands_raw],
        trend=trend,
        excluded_seasons=excluded,
        control_available=control_available,
        control_basis=control_basis,
        temporal_agreement=float(temporal["agreement"]),
        n_scenes_total=int(lineage.get("n_scenes", 0)),
        provenance=str(lineage.get("provenance", "unrecorded")),
    )
