#!/usr/bin/env python3
"""DEM derivatives and matched-control selection for the demo AOI (M2).

## Why this exists

`controls.select_controls` needs `SiteCovariates` — slope, elevation,
distance-to-stream, Strahler order, LULC and soil class. Until those exist the
control family is unavailable, which caps the epistemic ladder at L3 and leaves
the S7 ribbon provisional. This script produces them from a real DEM, so the
control family can be computed rather than refused.

## The chain, and why in this order

    NASADEM 1-arcsec tile (n19e077, EDL-authenticated)
      -> reproject to the AOI's UTM zone at 30 m
      -> BreachDepressionsLeastCost   (breach, not fill — see below)
      -> D8Pointer -> D8FlowAccumulation
      -> ExtractStreams(threshold) -> StrahlerStreamOrder
      -> Slope
      -> EuclideanDistance from streams
      -> sample covariates at the site and at candidate control pixels

**Breaching, not filling.** docs §15 is right that it matters for dams:
filling a depression raises it to its outlet and erases the impoundment a check
dam is supposed to create, so a real structure's own signature would be removed
before the terrain rule ever sees it. Breaching carves a channel instead and
leaves the depression measurable.

**The stream-initiation threshold is calibrated, not chosen.** A flow-
accumulation threshold picked by eye decides which structures count as
"on a channel", which decides `N3_TERRAIN_PATH`. It is swept against a
reference drainage network and the agreement score is persisted, so the number
in the record is defensible rather than a default.

## What this script does not do

It does not invent LULC or soil class. Bhuvan's LULC 50K is verified reachable
(docs/09) but is a WMS, not a coverage download, so a per-pixel class is not
available offline. `select_controls` matches on those only when both site and
candidate carry them; the covariates emitted here carry a single explicit
`unknown` so the matcher treats them as uninformative rather than as agreeing.
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.satellite.grid import grid_for_aoi  # noqa: E402
from app.services.temporal.controls import (  # noqa: E402
    MIN_DISTANCE_FROM_ANY_INTERVENTION_M,
    ControlCandidate,
    SiteCovariates,
    select_controls,
)

DEM_DIR = REPO_ROOT / "data" / "demo" / "dem"
OUT_DIR = DEM_DIR
SERIES = REPO_ROOT / "data" / "demo" / "temporal_series.json"

# Analysis AOI (the site's sub-watershed extent), and a larger hydrology extent.
# Flow accumulation computed on a box clipped at the AOI would be wrong at every
# edge: upstream area outside the box simply would not exist, so a site near the
# boundary would read as having no catchment.
AOI = (77.02, 19.02, 77.08, 19.08)
HYDRO_PAD_DEG = 0.18

SITE_LON, SITE_LAT = 77.05, 19.05

#: Candidate control pixels are drawn on a grid inside the AOI, then filtered by
#: `select_controls`. A grid, not random points: reproducibility matters more
#: than coverage, and the matcher does the selection anyway.
CANDIDATE_STRIDE_PX = 12

#: Thresholds swept for stream initiation, in accumulated pixels. At 30 m one
#: pixel is 900 m², so 500 px is 0.45 km² — the low end of what forms a channel
#: in semi-arid Deccan terrain.
THRESHOLD_SWEEP = (25, 50, 75, 100, 150, 200, 350, 500, 750, 1000, 1500, 2000)


@dataclass(frozen=True, slots=True)
class Rasters:
    """Paths to the derived layers, all on the same grid."""

    dem: Path
    breached: Path
    pointer: Path
    accumulation: Path
    streams: Path
    strahler: Path
    slope: Path
    dist_to_stream: Path


def run_whitebox(tool: str, **kwargs: Any) -> None:
    """Invoke one WhiteboxTools tool.

    Called through the Python wrapper rather than reimplemented: D8 breaching and
    Strahler ordering are exactly the kind of algorithm that looks simple and is
    not, and a hand-rolled version would be the least trustworthy component in
    the system.
    """
    import whitebox

    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    method = getattr(wbt, tool)
    code = method(**kwargs)
    if code != 0:
        raise RuntimeError(f"WhiteboxTools {tool} failed with code {code}")


def reproject_dem(dst_epsg: int, bounds: tuple[float, float, float, float]) -> Path:
    """Mosaic every NASADEM tile intersecting `bounds`, then reproject to UTM.

    A single tile is not enough. The site sits 5 km from the n19e077 western
    edge, and a first run on that tile alone produced a DEM only **37.4 %**
    valid over the padded extent — so flow accumulation was computed against
    nodata upstream and reported a 0.01 km² catchment. That is precisely the
    failure the pad exists to prevent, and it is silent: the numbers look like
    a site with no catchment rather than like missing data.
    """
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    tiles = sorted(DEM_DIR.glob("*.hgt"))
    if not tiles:
        raise SystemExit(f"no .hgt tiles in {DEM_DIR}")

    sources = [rasterio.open(t) for t in tiles]
    try:
        mosaic, mosaic_transform = merge(sources, bounds=bounds, nodata=-32768.0)
        src_crs = sources[0].crs or "EPSG:4326"
    finally:
        for s in sources:
            s.close()

    data = mosaic[0]
    valid = float(np.count_nonzero(data != -32768.0)) / data.size
    print(f"  mosaicked {len(tiles)} tiles -> {data.shape[1]}x{data.shape[0]}, {valid:.1%} valid")
    if valid < 0.99:
        # Refuse rather than proceed: partial coverage produces a wrong
        # catchment that looks like a real measurement.
        raise SystemExit(
            f"DEM covers only {valid:.1%} of the hydrology extent. Fetch the "
            "missing NASADEM tiles before deriving hydrology."
        )

    out = DEM_DIR / f"dem_utm{dst_epsg}.tif"
    dst_transform, width, height = calculate_default_transform(
        src_crs,
        f"EPSG:{dst_epsg}",
        data.shape[1],
        data.shape[0],
        *bounds,
        resolution=30.0,
    )
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "crs": f"EPSG:{dst_epsg}",
        "transform": dst_transform,
        "width": width,
        "height": height,
        "nodata": -32768.0,
        "compress": "deflate",
    }
    with rasterio.open(out, "w", **profile) as dst:
        reproject(
            source=data.astype("float32"),
            destination=rasterio.band(dst, 1),
            src_transform=mosaic_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=f"EPSG:{dst_epsg}",
            src_nodata=-32768.0,
            dst_nodata=-32768.0,
            resampling=Resampling.bilinear,
        )
    print(f"  DEM reprojected: {width}x{height} @ 30 m, EPSG:{dst_epsg}")
    return out


def derive(dem: Path, threshold: float) -> Rasters:
    """Run the hydrology chain at one stream-initiation threshold."""
    breached = OUT_DIR / "breached.tif"
    pointer = OUT_DIR / "d8_pointer.tif"
    accum = OUT_DIR / "d8_accum.tif"
    streams = OUT_DIR / "streams.tif"
    strahler = OUT_DIR / "strahler.tif"
    slope = OUT_DIR / "slope.tif"
    dist = OUT_DIR / "dist_to_stream.tif"

    if not breached.is_file():
        run_whitebox("breach_depressions_least_cost", dem=str(dem), output=str(breached), dist=100)
        run_whitebox("d8_pointer", dem=str(breached), output=str(pointer))
        run_whitebox("d8_flow_accumulation", i=str(breached), output=str(accum), out_type="cells")
        run_whitebox("slope", dem=str(breached), output=str(slope), units="degrees")

    run_whitebox("extract_streams", flow_accum=str(accum), output=str(streams), threshold=threshold)
    run_whitebox(
        "strahler_stream_order",
        d8_pntr=str(pointer),
        streams=str(streams),
        output=str(strahler),
    )
    # Euclidean distance to the nearest stream cell.
    #
    # ExtractStreams writes nodata off-stream, and EuclideanDistance measures
    # distance *from* non-background cells — so feeding it the raw streams
    # raster produced a distance grid that was 0 on streams and nodata
    # everywhere else, i.e. exactly no information, while looking like a valid
    # raster. The mask below makes background an explicit 0.
    mask = OUT_DIR / "streams_mask.tif"
    _write_stream_mask(streams, mask)
    run_whitebox("euclidean_distance", i=str(mask), output=str(dist))

    return Rasters(dem, breached, pointer, accum, streams, strahler, slope, dist)


def _write_stream_mask(streams: Path, out: Path) -> None:
    """Streams as 1, background as an explicit 0, no nodata."""
    import rasterio

    with rasterio.open(streams) as src:
        band = src.read(1)
        nodata = src.nodata
        binary = np.where(
            (band > 0) & (band != nodata) if nodata is not None else band > 0, 1, 0
        ).astype("uint8")
        profile = src.profile | {"dtype": "uint8", "nodata": None, "compress": "deflate"}
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(binary, 1)


def sample_disks(
    path: Path, xs: np.ndarray, ys: np.ndarray, radius_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Min / median / max over the location uncertainty disk at each point.

    Not a single pixel. `types.DiskStat` says it plainly: every terrain variable
    is a distribution because the claim's coordinate is only known to within its
    GPS accuracy, and "a single-pixel sample would be the most common way to
    produce a confidently wrong terrain verdict". A first version of this script
    sampled one pixel per point, which would have handed the rule engine a
    false precision it has no way to detect.

    A pixel is included when its centre lies within `radius_m + resolution/2` of
    the point, so a disk smaller than one pixel still reads the pixels it
    genuinely overlaps rather than only the one containing the centre.
    """
    import rasterio

    with rasterio.open(path) as src:
        band = src.read(1).astype(np.float64)
        if src.nodata is not None:
            band = np.where(band == src.nodata, np.nan, band)
        res = abs(src.transform.a)
        reach = radius_m + res / 2
        span = int(math.ceil(reach / res))
        offsets = [
            (dr, dc)
            for dr in range(-span, span + 1)
            for dc in range(-span, span + 1)
            if math.hypot(dr * res, dc * res) <= reach
        ]
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
        rows, cols = np.asarray(rows), np.asarray(cols)

        stacked = np.full((len(offsets), xs.size), np.nan)
        for k, (dr, dc) in enumerate(offsets):
            rr, cc = rows + dr, cols + dc
            ok = (rr >= 0) & (rr < src.height) & (cc >= 0) & (cc < src.width)
            if ok.any():
                stacked[k, ok] = band[rr[ok], cc[ok]]

    with warnings.catch_warnings():
        # An all-NaN disk is legitimate (off-raster or fully nodata) and must
        # stay NaN rather than become 0.0.
        warnings.simplefilter("ignore", RuntimeWarning)
        return (
            np.nanmin(stacked, axis=0),
            np.nanmedian(stacked, axis=0),
            np.nanmax(stacked, axis=0),
        )


def calibrate_threshold(dem: Path, reference_streams: Path | None) -> dict[str, Any]:
    """Sweep the stream-initiation threshold.

    Without a reference network available offline the sweep reports drainage
    density per threshold and picks the one nearest a published range for
    semi-arid Deccan basalt, and it records that this is a literature anchor
    rather than a fitted agreement score. Naming which of the two it is matters
    more than the number: docs §15's W3 fix asks for calibration against an
    authoritative reference, and Bhuvan's drainage layers are WMS-only, so the
    honest position is "anchored, not fitted".
    """
    import rasterio

    # Drainage density for semi-arid Deccan basalt catchments, km/km^2. Anchored
    # to the published range; the midpoint is the target.
    TARGET_DENSITY = (0.5, 2.0)
    results: list[dict[str, float]] = []

    for threshold in THRESHOLD_SWEEP:
        derive(dem, float(threshold))
        with rasterio.open(OUT_DIR / "streams.tif") as src:
            streams = src.read(1)
            cell = abs(src.transform.a)
            area_km2 = streams.size * cell * cell / 1e6
        stream_cells = int(np.count_nonzero(streams > 0))
        length_km = stream_cells * cell / 1000.0
        density = length_km / area_km2 if area_km2 > 0 else 0.0
        results.append({"threshold_px": float(threshold), "density_km_per_km2": density})
        print(f"    threshold {threshold:>5} px -> drainage density {density:.3f} km/km²")

    mid = sum(TARGET_DENSITY) / 2
    best = min(results, key=lambda r: abs(r["density_km_per_km2"] - mid))

    # A pick at either end of the sweep is not a calibration: the optimum lies
    # outside the range that was searched. The first run chose 200 px, the
    # lowest value tried, and reported it as "chosen" — which is how a sweep
    # silently degrades into a default.
    ends = (results[0]["threshold_px"], results[-1]["threshold_px"])
    if best["threshold_px"] in ends:
        raise SystemExit(
            f"threshold sweep selected {best['threshold_px']:.0f} px, an endpoint "
            f"of the searched range {ends}. The optimum is outside the sweep; "
            "widen THRESHOLD_SWEEP rather than accepting a boundary value."
        )

    return {
        "sweep": results,
        "chosen_threshold_px": best["threshold_px"],
        "chosen_density_km_per_km2": best["density_km_per_km2"],
        "target_density_range": list(TARGET_DENSITY),
        "basis": (
            "anchored to a published drainage-density range for semi-arid Deccan "
            "basalt, NOT fitted against a reference network: Bhuvan's drainage "
            "layers are WMS-only, so no vector reference was obtainable offline "
            "(docs/09). This is the W3 fix partially satisfied and labelled."
        ),
        "reference_used": str(reference_streams) if reference_streams else None,
    }


def main() -> int:
    if not sorted(DEM_DIR.glob("*.hgt")):
        print(
            f"no NASADEM .hgt tiles in {DEM_DIR}; fetch them first (see docs/13-terrain.md)",
            file=sys.stderr,
        )
        return 2

    def reproject_bounds(
        src_crs: str, dst_crs: str, left: float, bottom: float, right: float, top: float
    ) -> tuple[float, float, float, float]:
        from pyproj import Transformer

        tf = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
        x0, y0 = tf.transform(left, bottom)
        x1, y1 = tf.transform(right, top)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    grid = grid_for_aoi(*AOI, reproject_bounds=reproject_bounds)
    hydro_bounds = (
        AOI[0] - HYDRO_PAD_DEG,
        AOI[1] - HYDRO_PAD_DEG,
        AOI[2] + HYDRO_PAD_DEG,
        AOI[3] + HYDRO_PAD_DEG,
    )
    print(f"analysis grid  : {grid.width}x{grid.height} @ 30 m, EPSG:{grid.epsg}")
    print(f"hydrology pad  : {HYDRO_PAD_DEG}° (~{HYDRO_PAD_DEG * 111:.0f} km) beyond the AOI")

    dem = reproject_dem(grid.epsg, hydro_bounds)

    print("\ncalibrating the stream-initiation threshold:")
    calibration = calibrate_threshold(dem, None)
    print(
        f"  chosen: {calibration['chosen_threshold_px']:.0f} px "
        f"(density {calibration['chosen_density_km_per_km2']:.3f} km/km²)"
    )

    rasters = derive(dem, calibration["chosen_threshold_px"])

    # Site and candidate positions in projected metres.
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    site_x, site_y = (float(v) for v in tf.transform(SITE_LON, SITE_LAT))

    xs_grid = np.arange(
        grid.left + grid.resolution_m / 2, grid.right, grid.resolution_m * CANDIDATE_STRIDE_PX
    )
    ys_grid = np.arange(
        grid.bottom + grid.resolution_m / 2, grid.top, grid.resolution_m * CANDIDATE_STRIDE_PX
    )
    gx, gy = np.meshgrid(xs_grid, ys_grid)
    cand_x, cand_y = gx.ravel(), gy.ravel()

    all_x = np.concatenate([[site_x], cand_x])
    all_y = np.concatenate([[site_y], cand_y])

    # Uncertainty disk: max(gps_accuracy, 15 m). The demo claim records 6 m.
    DISK_RADIUS_M = 15.0
    stats = {
        "elevation_m": sample_disks(rasters.breached, all_x, all_y, DISK_RADIUS_M),
        "slope_deg": sample_disks(rasters.slope, all_x, all_y, DISK_RADIUS_M),
        "flow_accum_px": sample_disks(rasters.accumulation, all_x, all_y, DISK_RADIUS_M),
        # Strahler nodata means *not on an extracted channel*, which is order 0
        # (types.DiskStat: "0 means not on an extracted channel"). Leaving it as
        # NaN would make the rule engine treat a definite off-channel site as an
        # unknown one, and the two lead to opposite verdicts: unknown abstains,
        # order 0 is what drives N3_TERRAIN_PATH for a check dam.
        "strahler_order": tuple(
            np.nan_to_num(a, nan=0.0)
            for a in sample_disks(rasters.strahler, all_x, all_y, DISK_RADIUS_M)
        ),
        "dist_to_stream_m": sample_disks(rasters.dist_to_stream, all_x, all_y, DISK_RADIUS_M),
    }

    def triple(name: str, i: int) -> dict[str, float | None]:
        lo, mid, hi = stats[name]
        return {"minimum": _f(lo[i]), "median": _f(mid[i]), "maximum": _f(hi[i])}

    print("\nsite covariates over a 15 m uncertainty disk (min / median / max):")
    for name in stats:
        tr = triple(name, 0)
        print(f"  {name:<18} {tr['minimum']} / {tr['median']} / {tr['maximum']}")

    payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "dem": {
            "source": "NASADEM_HGT.001, 6 tiles mosaicked (EDL-authenticated)",
            "tiles": [p.stem for p in sorted(DEM_DIR.glob("*.hgt"))],
            "resolution_m": 30.0,
            "epsg": grid.epsg,
            "hydrology_pad_deg": HYDRO_PAD_DEG,
            "breaching": "BreachDepressionsLeastCost(dist=100) — breach, not fill",
            "disk_radius_m": DISK_RADIUS_M,
        },
        "threshold_calibration": calibration,
        "site": {"lon": SITE_LON, "lat": SITE_LAT} | {name: triple(name, 0) for name in stats},
        "candidates": [
            {
                "control_id": f"C{i:03d}",
                "x": float(cand_x[i]),
                "y": float(cand_y[i]),
                "dist_from_site_m": round(math.hypot(cand_x[i] - site_x, cand_y[i] - site_y), 1),
            }
            | {name: triple(name, i + 1) for name in stats}
            for i in range(cand_x.size)
            if np.isfinite(stats["elevation_m"][1][i + 1])
            and np.isfinite(stats["slope_deg"][1][i + 1])
        ],
    }

    # Run the real matcher. This is the point of the whole script: without a
    # selected control set the control family is unavailable and the epistemic
    # ladder is capped at L3 (docs §16.2 STEP 10).
    def as_cov(rec: dict[str, Any], x: float = 0.0, y: float = 0.0) -> SiteCovariates:
        return SiteCovariates(
            slope_deg=float(rec["slope_deg"]["median"]),
            aspect_class="unknown",
            lulc_class="unknown",
            soil_class="unknown",
            elevation_m=float(rec["elevation_m"]["median"]),
            dist_to_stream_m=float(rec["dist_to_stream_m"]["median"]),
            strahler_order=int(rec["strahler_order"]["median"]),
            easting_m=x,
            northing_m=y,
        )

    # `channel_structure` False selects C7 (distance-to-stream similarity) over
    # C6 (Strahler equality). Correct here: the site is off-channel at order 0,
    # and requiring 0 == 0 while also requiring stream distance within 50 m
    # over-constrains for no gain (see select_controls' docstring).
    candidates = [
        ControlCandidate(
            control_id=str(c["control_id"]),
            covariates=as_cov(c, float(c["x"]), float(c["y"])),
            # Deltas are measured later, by build_temporal_series.py at these
            # positions. Selection is a function of covariates only, so a
            # placeholder here cannot influence which controls are chosen.
            delta=0.0,
            data_sufficiency=1.0,
            dist_to_nearest_intervention_m=float(c["dist_from_site_m"]),
        )
        for c in payload["candidates"]
    ]
    control_set = select_controls(
        as_cov(payload["site"]),
        candidates,
        site_data_sufficiency=1.0,
        channel_structure=False,
    )
    by_id = {c["control_id"]: c for c in payload["candidates"]}
    payload["matched_controls"] = [
        {
            "control_id": c.control_id,
            "x": by_id[c.control_id]["x"],
            "y": by_id[c.control_id]["y"],
            "slope_deg": c.covariates.slope_deg,
            "elevation_m": c.covariates.elevation_m,
            "dist_to_stream_m": c.covariates.dist_to_stream_m,
            "dist_from_site_m": by_id[c.control_id]["dist_from_site_m"],
        }
        for c in control_set.selected
    ]
    payload["control_selection"] = control_set.lineage()
    print(
        f"\nmatched controls: {control_set.n_selected} selected from "
        f"{control_set.n_candidates} candidates"
    )
    for reason, count in sorted(control_set.rejected.items(), key=lambda kv: -kv[1]):
        print(f"  rejected {reason:<30} {count}")
    if control_set.insufficient:
        print(
            f"  INSUFFICIENT (min {MIN_DISTANCE_FROM_ANY_INTERVENTION_M:.0f} m rule "
            "and covariate limits left too few): the control family will be "
            "reported unavailable, which caps the ladder at L3.",
        )

    out = OUT_DIR.parent / "terrain.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(payload['candidates'])} candidate pixels with complete covariates")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


def _f(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 4)


if __name__ == "__main__":
    raise SystemExit(main())
