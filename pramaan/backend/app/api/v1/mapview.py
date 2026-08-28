"""Plan-view map layers — the thematic output the problem statement asks for.

PS 26015 asks for *"land use maps, drainage maps, watershed intervention maps
and spatial change detection products"*. This endpoint serves the geometry those
products are drawn from.

## Every layer is measured

- **Drainage** — the D8 network extracted from six mosaicked NASADEM tiles by the
  same WhiteboxTools chain, at the same calibrated stream-initiation threshold,
  that produced the terrain verdict. Strahler order per segment.
- **Intervention** — the claim's own recorded coordinate.
- **Controls** — the twelve sites `controls.select_controls` actually chose,
  with the covariates they were matched on.

Nothing here is decorative. A map layer that exists only to look like a map is
the reason this console shipped without one until the data was real.

## No basemap, deliberately

Rendered as a survey plan on the product's own ground rather than over tiles.
Two reasons, and the first is the binding one:

1. §38 requires the demo to work with the network interface disabled. A tile
   request is exactly the dependency that fails on a venue network.
2. A slippy basemap makes this look like a consumer map product. A survey plan
   makes it look like what it is — a record.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import db_session

router = APIRouter(tags=["map"])

DbSession = Annotated[Session, Depends(db_session)]

LAYERS = Path(__file__).resolve().parents[3].parent / "data" / "demo" / "map_layers.json"

_CLAIM = text("""
SELECT DISTINCT ON (c.id)
       c.id                AS claim_id,
       i.unique_id         AS unique_id,
       i.type              AS intervention_type,
       c.uncertainty_m     AS uncertainty_m,
       i.expected_footprint_m2 AS expected_footprint_m2,
       ST_Y(c.geom::geometry) AS lat,
       ST_X(c.geom::geometry) AS lon,
       v.level             AS level,
       v.confidence        AS confidence
FROM claims c
JOIN interventions i ON i.id = c.intervention_id
LEFT JOIN verdicts v ON v.claim_id = c.id
WHERE c.id = :claim_id
ORDER BY c.id, v.version DESC
""")


class Segment(BaseModel):
    """One D8 step, from a stream cell to its downstream neighbour."""

    frm: list[float] = Field(alias="from")
    to: list[float]
    #: Strahler order. Drives line weight — the terrain rule tests this value,
    #: so the map weights what the analysis reads.
    order: int

    model_config = {"populate_by_name": True}


class ControlPoint(BaseModel):
    control_id: str
    lonlat: list[float]
    slope_deg: float
    elevation_m: float
    dist_to_stream_m: float
    dist_from_site_m: float


class SitePoint(BaseModel):
    lonlat: list[float]
    strahler_order: float
    dist_to_stream_m: float
    slope_deg: float


class MapOut(BaseModel):
    claim_id: int
    unique_id: str
    intervention_type: str
    level: str | None
    confidence: float | None
    uncertainty_m: float | None
    expected_footprint_m2: float | None

    #: [w, s, e, n] in WGS84.
    aoi: list[float]
    window: list[float]

    site: SitePoint
    controls: list[ControlPoint]
    drainage: list[Segment]

    #: Per-layer provenance, rendered in the legend. A map without stated
    #: provenance is a picture.
    provenance: dict[str, str]
    available: bool = True


@lru_cache(maxsize=1)
def _load() -> dict[str, Any] | None:
    """Layers are a build artefact; cache them rather than re-reading per request.

    Returns None when they have not been built, so the endpoint can say which of
    "no map" and "map broken" it is.
    """
    if not LAYERS.is_file():
        return None
    return dict(json.loads(LAYERS.read_text(encoding="utf-8")))


@router.get("/claims/{claim_id}/map", response_model=MapOut)
def get_map(claim_id: int, session: DbSession) -> MapOut:
    row = session.execute(_CLAIM, {"claim_id": claim_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"claim {claim_id} does not exist")

    layers = _load()
    if layers is None:
        # 422 rather than an empty map: an empty plan view reads as "nothing is
        # here", which is a different statement from "this was never built".
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "map layers have not been built. Run `make map` — it needs the "
            "NASADEM tiles, which are not committed.",
        )

    site = dict(layers["site"])
    # The stored claim coordinate wins over the one baked into the layers: the
    # layers are a snapshot of one AOI, and the claim is the authority on where
    # it is.
    site["lonlat"] = [float(row["lon"]), float(row["lat"])]

    return MapOut(
        claim_id=claim_id,
        unique_id=str(row["unique_id"]),
        intervention_type=str(row["intervention_type"]),
        level=row["level"],
        confidence=None if row["confidence"] is None else float(row["confidence"]),
        uncertainty_m=None if row["uncertainty_m"] is None else float(row["uncertainty_m"]),
        expected_footprint_m2=None
        if row["expected_footprint_m2"] is None
        else float(row["expected_footprint_m2"]),
        aoi=[float(v) for v in layers["aoi"]],
        window=[float(v) for v in layers["window"]],
        site=SitePoint(**site),
        controls=[ControlPoint(**c) for c in layers["controls"]],
        drainage=[Segment(**s) for s in layers["drainage"]],
        provenance=dict(layers["provenance"]),
    )
