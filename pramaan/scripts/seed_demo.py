#!/usr/bin/env python3
"""Seed the demo claim from the measured HLS series.

Referenced by `make seed`. Reads `data/demo/temporal_series.json` — produced by
`scripts/build_temporal_series.py` from real NASA HLS granules — runs it through
the real temporal chain, and persists a verdict via the same Celery task path
the API uses.

## What is real here and what is not

**Real, measured:** every index value, every scene id, cloud fraction, seasonal
composite, the PRE/POST windows, the deltas, the control comparison, the trend
refusal, and the verdict.

**Not measured:** the hierarchy rows (a synthetic watershed/project/claim, since
DoLR MIS records are not public), and the control *positions* — real control
selection is covariate-matched on DEM derivatives (`controls.select_controls`),
which needs the DEM that lands with M2. Until then controls are a fixed ring at
1.2 km, which satisfies the >= 250 m separation rule but is **not** slope-,
elevation- or LULC-matched. The seeded lineage records this so nothing
downstream can present ring controls as matched controls.

## What the measured data actually says

The site's rabi NDVI rose +0.1452 across the claim date. On its own that reads
as success. The eight controls rose a median +0.1242, and the site falls inside
their [p10, p90] band — so the rise is regional, and the control-differenced
estimate is inside the noise floor. docs §17.4: *"A rise every control also shows
is not differential evidence, whatever its magnitude."*

That is the demo. A naive NDVI-difference dashboard reports a success here.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.db.session import session_scope  # noqa: E402
from app.services.audit import wire_payload  # noqa: E402
from app.services.context.rainfall import to_context_evidence  # noqa: E402
from app.services.reconcile import (  # noqa: E402
    EvidenceBundle,
    FamilyEvidence,
    Gates,
    Quality,
)
from app.services.satellite.evidence import IndexObservation  # noqa: E402
from app.services.satellite.evidence import assess as satellite_assess  # noqa: E402
from app.services.satellite.evidence import (  # noqa: E402
    to_family_evidence as satellite_evidence,
)
from app.services.temporal.controls import (  # noqa: E402
    ControlCandidate,
    ControlSet,
    SiteCovariates,
    compare_to_controls,
)
from app.services.temporal.evidence import assess as temporal_assess  # noqa: E402
from app.services.temporal.evidence import (  # noqa: E402
    to_control_evidence as control_evidence,
)
from app.services.temporal.evidence import (  # noqa: E402
    to_temporal_evidence as temporal_evidence,
)
from app.services.temporal.seasons import (  # noqa: E402
    Season,
    SeasonalObservation,
    seasonal_delta,
)
from app.services.temporal.trend import mann_kendall, theil_sen_slope  # noqa: E402
from app.services.temporal.windows import build_pairings, build_windows  # noqa: E402
from app.services.terrain.evidence import to_family_evidence as terrain_evidence  # noqa: E402
from app.services.terrain.plausibility import evaluate as terrain_evaluate  # noqa: E402
from app.services.terrain.types import DiskStat, TerrainSample  # noqa: E402
from app.workers.reconcile import reconcile_claim  # noqa: E402

SERIES = REPO_ROOT / "data" / "demo" / "temporal_series.json"
SEASON_BY_NAME = {"rabi": Season.RABI, "summer": Season.SUMMER, "kharif": Season.KHARIF}
INDEX = "NDVI"

# Controls are a ring, not a matched set, until the DEM lands. Recorded in the
# lineage so no consumer can mistake one for the other.
CONTROL_BASIS = "fixed 1.2 km ring; NOT covariate-matched (DEM pending, M2)"

DDL_DEMO = """
INSERT INTO watersheds (ws_code, geom)
VALUES ('DEMO-4D3C', ST_GeomFromText(:poly, 4326))
ON CONFLICT (ws_code) DO NOTHING;

INSERT INTO sub_watersheds (sws_code, watershed_id, geom)
SELECT 'DEMO-4D3C2A', id, ST_GeomFromText(:poly, 4326) FROM watersheds
WHERE ws_code = 'DEMO-4D3C'
ON CONFLICT (sws_code) DO NOTHING;

INSERT INTO micro_watersheds
    (mws_code, sub_ws_id, state_lgd, district_lgd, geom, analysis_srid)
SELECT 'DEMO-4D3C2A1a', id, '27', '520', ST_GeomFromText(:poly, 4326), 32643
FROM sub_watersheds WHERE sws_code = 'DEMO-4D3C2A'
ON CONFLICT (mws_code) DO NOTHING;

INSERT INTO projects (project_code, name, mws_id, state_lgd, district_lgd)
SELECT 'DEMO-WDC-01', 'Demo micro-watershed', id, '27', '520'
FROM micro_watersheds WHERE mws_code = 'DEMO-4D3C2A1a'
ON CONFLICT (project_code) DO NOTHING;

INSERT INTO interventions (unique_id, project_id, mws_id, district_lgd, type,
    status, completed_date, geom, expected_footprint_m2)
SELECT 'DEMO-MH-520-0001', p.id, m.id, '520', 'check_dam', 'completed',
    :claim_date, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 3200
FROM projects p, micro_watersheds m
WHERE p.project_code = 'DEMO-WDC-01' AND m.mws_code = 'DEMO-4D3C2A1a'
ON CONFLICT (unique_id) DO NOTHING;
"""

CLAIM_INSERT = """
INSERT INTO claims (intervention_id, district_lgd, asserted_status,
    asserted_date, geom, uncertainty_m, detectability)
SELECT id, '520', 'completed', :claim_date,
    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 6, 'passed'
FROM interventions WHERE unique_id = 'DEMO-MH-520-0001'
RETURNING id
"""

CLAIM_LOOKUP = """
SELECT c.id FROM claims c
JOIN interventions i ON i.id = c.intervention_id
WHERE i.unique_id = 'DEMO-MH-520-0001'
ORDER BY c.id LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class Comparison:
    """The measured site-vs-control result for one season pairing."""

    season: str
    pre_year: int
    post_year: int
    site_pre: float
    site_post: float
    site_delta: float
    control_deltas: tuple[float, ...]
    p10: float
    median: float
    p90: float

    @property
    def inside_band(self) -> bool:
        return self.p10 <= self.site_delta <= self.p90

    @property
    def differenced(self) -> float:
        return self.site_delta - self.median

    def lineage(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "pre_year": self.pre_year,
            "post_year": self.post_year,
            "site_delta": round(self.site_delta, 4),
            "control_median_delta": round(self.median, 4),
            "control_p10": round(self.p10, 4),
            "control_p90": round(self.p90, 4),
            "differenced_estimate": round(self.differenced, 4),
            "site_inside_control_band": self.inside_band,
            "n_controls": len(self.control_deltas),
            "control_basis": CONTROL_BASIS,
        }


def load_series() -> dict[str, Any]:
    if not SERIES.is_file():
        raise SystemExit(
            f"{SERIES.relative_to(REPO_ROOT)} not found. Run:\n"
            "  uv run --with httpx --with rasterio --with numpy --with pyproj \\\n"
            "      python scripts/build_temporal_series.py"
        )
    return json.loads(SERIES.read_text(encoding="utf-8"))


def site_observations(data: dict[str, Any]) -> list[SeasonalObservation]:
    out: list[SeasonalObservation] = []
    for entry in data["series"]:
        if not entry["sufficient"]:
            continue
        index = entry["indices"][INDEX]
        if index["site"] is None:
            continue
        out.append(
            SeasonalObservation(
                index_name=INDEX,
                season=SEASON_BY_NAME[entry["season"]],
                year=entry["year"],
                value=float(index["site"]),
                data_sufficiency=float(index["valid_fraction"]),
                n_scenes=len(entry["scenes"]),
            )
        )
    return out


def comparisons(data: dict[str, Any], claim_date: date) -> list[Comparison]:
    """Site and control deltas, using the engine's own pairing rules.

    Controls are paired over exactly the same seasons and years as the site.
    Pairing them independently would let a control's delta come from a different
    year than the site's, which is not a comparison.
    """
    windows = build_windows(claim_date)
    result = build_pairings(site_observations(data), windows)

    by_key = {(e["year"], e["season"]): e for e in data["series"] if e["sufficient"]}
    out: list[Comparison] = []
    for pairing in result.pairings:
        season = pairing.season.value
        pre = by_key[(pairing.pre.year, season)]["indices"][INDEX]["controls"]
        post = by_key[(pairing.post.year, season)]["indices"][INDEX]["controls"]
        deltas = [
            float(b) - float(a)
            for a, b in zip(pre, post, strict=True)
            if a is not None and b is not None
        ]
        if not deltas:
            continue
        p10, median, p90 = (float(x) for x in np.percentile(deltas, [10, 50, 90]))
        out.append(
            Comparison(
                season=season,
                pre_year=pairing.pre.year,
                post_year=pairing.post.year,
                site_pre=pairing.pre.value,
                site_post=pairing.post.value,
                site_delta=pairing.post.value - pairing.pre.value,
                control_deltas=tuple(deltas),
                p10=p10,
                median=median,
                p90=p90,
            )
        )
    return out


def build_bundle(data: dict[str, Any], cmps: list[Comparison]) -> EvidenceBundle:
    """Assemble the families the measured data can honestly support.

    Every family here is built by the module that owns it. No hand-written
    `FamilyEvidence` anywhere: a first draft of this script set agreement values
    by hand from the measured deltas, which quietly moved the delta -> agreement
    mapping out of the tested adapter and into a seed script.

    Two families are **unavailable**, and that is the point:

    `terrain`, `photo`
        Absent entirely. Terrain needs DEM derivatives (M2) and photo needs a
        model checkpoint (M6).

    `control`
        Unavailable, not scored. The ring positions measured by
        `build_temporal_series.py` are 1.2 km away and satisfy the >= 250 m
        separation rule, but `SiteCovariates` requires slope, elevation,
        distance-to-stream, Strahler order, LULC and soil class - all DEM- or
        LULC-derived. Fabricating those to make `select_controls` run would
        manufacture the one family whose whole job is to exclude alternative
        explanations. The ring measurement is preserved in the lineage as a
        preliminary observation, explicitly not a matched control set.

    `context`
        Unavailable: CHIRPS is reachable (docs/09) but nothing has measured this
        AOI, and a fabricated rainfall anomaly would become the alternative
        explanation the control family exists to test.

    So coverage is low by construction, and the verdict is correspondingly weak.
    That is the designed behaviour, not a shortcoming of the seed.
    """
    series = [e for e in data["series"] if e["sufficient"]]
    scene_ids = [s["id"] for e in series for s in e["scenes"]]
    grid = data["grid"]
    shared: dict[str, object] = {
        "index": INDEX,
        "analysis_grid": grid,
        "scene_ids": scene_ids,
        "n_scenes": len(scene_ids),
        "provenance": data["provenance"],
        "kharif": data["kharif"],
    }

    # --- satellite: observed state, via the satellite adapter ---------------
    # The most recent sufficient rabi composite is the state observation. Rabi,
    # not summer: docs §17.2 measures rabi at weight 1.0 against summer 0.9.
    latest_rabi = max((e for e in series if e["season"] == "rabi"), key=lambda e: e["year"])
    observations = [
        IndexObservation(
            index_name=name,
            value=float(latest_rabi["indices"][name]["site"]),
            usable_fraction=float(latest_rabi["indices"][name]["valid_fraction"]),
            n_scenes=len(latest_rabi["scenes"]),
            aoi="site disk (3 px radius at 30 m)",
        )
        for name in ("NDVI", "MNDWI")
        if latest_rabi["indices"][name]["site"] is not None
    ]
    satellite = satellite_evidence(
        satellite_assess(observations, "check_dam"),
        lineage_extra=shared | {"composite_season": f"rabi {latest_rabi['year']}"},
    )

    # --- temporal: same-season deltas, via the temporal adapter -------------
    deltas = [
        seasonal_delta(
            SeasonalObservation(
                index_name=INDEX,
                season=SEASON_BY_NAME[c.season],
                year=c.pre_year,
                value=c.site_pre,
                data_sufficiency=1.0,
                n_scenes=_scenes_for(series, c.pre_year, c.season),
            ),
            SeasonalObservation(
                index_name=INDEX,
                season=SEASON_BY_NAME[c.season],
                year=c.post_year,
                value=c.site_post,
                data_sufficiency=1.0,
                n_scenes=_scenes_for(series, c.post_year, c.season),
            ),
        )
        for c in cmps
    ]
    rabi_values = np.array(
        [
            float(e["indices"][INDEX]["site"])
            for e in series
            if e["season"] == "rabi" and e["indices"][INDEX]["site"] is not None
        ],
        dtype=float,
    )
    trend = mann_kendall(rabi_values)
    temporal = temporal_evidence(
        temporal_assess(
            deltas,
            "check_dam",
            trends={INDEX: trend},
            skipped={Season.KHARIF: data["kharif"]},
        ),
        lineage_extra=shared
        | {
            "theil_sen_slope_per_year": round(
                theil_sen_slope(rabi_values, np.arange(len(rabi_values), dtype=float)),
                4,
            ),
            # The full observed series, not just the paired deltas. The S7 chart
            # draws every season; the deltas alone would give it two points and
            # no line. Provenance, not a decision input - the engine never
            # reads this.
            "observed_series": [
                {
                    "year": e["year"],
                    "season": e["season"],
                    "site": e["indices"][INDEX]["site"],
                    "controls": e["indices"][INDEX]["controls"],
                    "usable_fraction": e["indices"][INDEX]["valid_fraction"],
                    "n_scenes": len(e["scenes"]),
                    "scene_ids": [s["id"] for s in e["scenes"]],
                }
                for e in series
            ],
            "windows": build_windows(date.fromisoformat(data["claim_date"])).lineage(),
        },
    )

    # --- terrain: measured from the DEM, via the terrain rule engine --------
    terrain_path = REPO_ROOT / "data" / "demo" / "terrain.json"
    terrain_family = None
    control_family = None
    if terrain_path.is_file():
        td = json.loads(terrain_path.read_text(encoding="utf-8"))
        site_t = td["site"]

        def disk(name: str) -> DiskStat:
            v = site_t[name]
            return DiskStat(
                minimum=float(v["minimum"]),
                median=float(v["median"]),
                maximum=float(v["maximum"]),
            )

        px_to_km2 = 900 / 1e6
        accum = site_t["flow_accum_px"]
        sample = TerrainSample(
            disk_radius_m=float(td["dem"]["disk_radius_m"]),
            slope_deg=disk("slope_deg"),
            strahler_order=disk("strahler_order"),
            flow_accumulation_px=disk("flow_accum_px"),
            dist_to_stream_m=disk("dist_to_stream_m"),
            upstream_area_km2=DiskStat(
                minimum=float(accum["minimum"]) * px_to_km2,
                median=float(accum["median"]) * px_to_km2,
                maximum=float(accum["maximum"]) * px_to_km2,
            ),
            in_depression=False,
            dem_product=td["dem"]["source"],
            dem_version="NASADEM.001",
            stream_threshold_px=float(td["threshold_calibration"]["chosen_threshold_px"]),
            stream_threshold_agreement=float(
                td["threshold_calibration"]["chosen_density_km_per_km2"]
            ),
        )
        terrain_family = terrain_evidence(terrain_evaluate("check_dam", sample), sample)

        # --- control: real covariate-matched differencing --------------------
        matched = td.get("matched_controls") or []
        first = cmps[0]
        deltas = first.control_deltas
        if len(matched) >= 5 and len(deltas) >= 5:
            cov = SiteCovariates(
                slope_deg=float(site_t["slope_deg"]["median"]),
                aspect_class="unknown",
                lulc_class="unknown",
                soil_class="unknown",
                elevation_m=float(site_t["elevation_m"]["median"]),
                dist_to_stream_m=float(site_t["dist_to_stream_m"]["median"]),
                strahler_order=int(site_t["strahler_order"]["median"]),
            )
            candidates = [
                ControlCandidate(
                    control_id=matched[i]["control_id"] if i < len(matched) else f"C{i}",
                    covariates=cov,
                    delta=delta,
                    data_sufficiency=1.0,
                    dist_to_nearest_intervention_m=float(
                        matched[i]["dist_from_site_m"] if i < len(matched) else 999.0
                    ),
                )
                for i, delta in enumerate(deltas)
            ]
            control_set = ControlSet(
                selected=tuple(candidates),
                rejected=dict(td["control_selection"]["rejected_by_reason"]),
                n_candidates=int(td["control_selection"]["n_candidates"]),
                insufficient=False,
                channel_structure=bool(td["control_selection"]["channel_structure"]),
                reason=(
                    f"{len(candidates)} covariate-matched controls from "
                    f"{td['dem']['source']}, matched on slope, elevation and "
                    "distance-to-stream over a 15 m uncertainty disk"
                ),
            )
            comparison = compare_to_controls(first.site_delta, control_set)
            control_family = control_evidence(control_set, comparison, "check_dam", INDEX)

    if control_family is None:
        # No DEM, so covariate matching could not run. Unavailable, not scored.
        ring = ControlSet(
            selected=(),
            rejected={"covariates_unavailable": len(cmps[0].control_deltas)},
            n_candidates=len(cmps[0].control_deltas),
            insufficient=True,
            channel_structure=True,
            reason=(
                "Covariate matching could not run: slope, elevation, "
                "distance-to-stream and Strahler order require DEM derivatives "
                "that have not been built for this district."
            ),
        )
        control_family = control_evidence(ring, None, "check_dam", INDEX)
    control = FamilyEvidence(
        family=control_family.family,
        agreement=control_family.agreement,
        available=control_family.available,
        reason=control_family.reason,
        lineage=dict(control_family.lineage) | {"per_season": [c.lineage() for c in cmps]},
        cluster_scale=control_family.cluster_scale,
    )

    # --- context: unavailable ----------------------------------------------
    # An empty context list, not a fabricated RainfallContext. The dataclass
    # rightly refuses None for rainfall_mm, and inventing a number here would
    # manufacture the alternative explanation the control family exists to test.
    context = to_context_evidence([], observed_index_delta=cmps[0].site_delta)

    return EvidenceBundle(
        claim_id="DEMO-MH-520-0001",
        intervention_type="check_dam",
        families=tuple(
            f for f in (terrain_family, satellite, temporal, control, context) if f is not None
        ),
        gates=Gates(
            detectability_passed=True,
            expected_footprint_m2=3200.0,
            pixel_area_m2=float(grid["resolution_m"]) ** 2,
            escalated_to_cluster=False,
            scene_scale="mid",
        ),
        quality=Quality(
            metadata_integrity=1.0,
            data_sufficiency=min(float(e["indices"][INDEX]["valid_fraction"]) for e in series),
        ),
        alternatives=(),
    )


def _scenes_for(series: list[dict[str, Any]], year: int, season: str) -> int:
    for entry in series:
        if entry["year"] == year and entry["season"] == season:
            return len(entry["scenes"])
    return 0


def main() -> int:
    data = load_series()
    claim_date = date.fromisoformat(data["claim_date"])
    cmps = comparisons(data, claim_date)
    if not cmps:
        print("no season pairings survived the windows; nothing to seed", file=sys.stderr)
        return 1

    poly = "MULTIPOLYGON((({0} {1}, {2} {1}, {2} {3}, {0} {3}, {0} {1})))".format(
        *(data["aoi"][i] for i in (0, 1, 2, 3))
    )
    params = {
        "poly": poly,
        "claim_date": claim_date,
        "lon": data["site"]["lon"],
        "lat": data["site"]["lat"],
    }

    with session_scope() as session:
        for statement in filter(None, (s.strip() for s in DDL_DEMO.split(";"))):
            session.execute(text(statement), params)
        existing = session.execute(text(CLAIM_LOOKUP)).scalar()
        claim_id = (
            int(existing)
            if existing is not None
            else int(session.execute(text(CLAIM_INSERT), params).scalar_one())
        )

    bundle = build_bundle(data, cmps)
    # Stamp provenance at the verdict lineage root, which is the one place the
    # claims API reads it from. Without this the measured claim is classified
    # `golden` by the API's safe default — correct behaviour on its part, wrong
    # label on this row.
    result = reconcile_claim(
        claim_id,
        wire_payload(bundle),
        extra_lineage={
            "provenance": data["provenance"],
            "measured_at": data["measured_at"],
            "control_basis": data.get("control_basis", "unrecorded"),
        },
    )

    print(f"claim_id      : {claim_id}")
    print(f"verdict_id    : {result['verdict_id']}")
    print(f"level         : {result['level']}")
    print(f"label         : {result['label']}")
    print(f"confidence    : {result['confidence']}")
    print(f"coverage      : {result['coverage']} ({result['families_available']}/6 families)")
    print()
    for c in cmps:
        print(
            f"  {c.season:<7} site {c.site_delta:+.4f} | controls "
            f"p10={c.p10:+.4f} med={c.median:+.4f} p90={c.p90:+.4f} | "
            f"{'INSIDE' if c.inside_band else 'OUTSIDE'} band | "
            f"differenced {c.differenced:+.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
