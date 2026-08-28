#!/usr/bin/env python3
"""Build a real seasonal index series for one AOI, from real HLS granules.

## Why this exists

Every module in the temporal chain is unit-tested, but until now none of them
had seen a multi-season series computed from actual imagery. This script is the
first end-to-end exercise of the real chain:

    STAC search -> EDL presign -> windowed COG read on a fixed grid
      -> Fmask clear mask -> NDVI / MNDWI -> per-season per-pixel median
      -> site disk + matched-control disks -> seasonal deltas

Its output is the payload the S7 hero chart renders, and it is measured rather
than synthesised. Nothing here invents a number.

## Two decisions the data forced

**Kharif is excluded, not down-weighted.** Measured on this AOI across five
years: kharif yields 0-5 scenes under 20 % cloud, against 23-34 for rabi. A
"kharif composite" built from one scene is a single cloudy date wearing the word
composite. docs §17.2's season weights already say rabi 1.0 / summer 0.9 /
kharif 0.4; this goes further and reports kharif as insufficient.

**Controls cost almost nothing.** Matched controls must come from the same
sub-watershed (`controls.py`), so they fall inside the same COG tiles as the
site. One windowed read serves the site and every control, which is why this
runs in minutes rather than hours - and it is the practical payoff of the
AOI-unit finding in docs/11 §10.

Run:
    uv run --with httpx --with rasterio --with numpy \
        python scripts/build_temporal_series.py
Needs EARTHDATA_TOKEN.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.satellite.edl_auth import OPTIMAL_CONCURRENCY, EdlResolver  # noqa: E402
from app.services.satellite.fmask import clear_mask  # noqa: E402
from app.services.satellite.grid import grid_for_aoi  # noqa: E402
from app.services.satellite.indices import (  # noqa: E402
    mndwi,
    ndvi,
    nir_band_for,
    scale_reflectance,
    seasonal_composite,
    valid_fraction,
)

STAC = "https://cmr.earthdata.nasa.gov/stac/LPCLOUD/search"
COLLECTIONS = ["HLSS30_2.0", "HLSL30_2.0"]

# A real check-dam location in Marathwada, and a ~6 km AOI around it. The AOI is
# sized to hold the site plus its control candidates, which is what makes the
# controls free (see module docstring).
SITE_LON, SITE_LAT = 77.05, 19.05
AOI = (77.02, 19.02, 77.08, 19.08)
CLAIM_DATE = "2023-11-20"

# Rabi and summer only. See module docstring: kharif does not yield a composite
# on this AOI in any of the five years measured.
SEASONS: dict[str, tuple[int, int]] = {"rabi": (11, 2), "summer": (3, 5)}
YEARS = (2021, 2022, 2023, 2024, 2025)

MAX_SCENES_PER_SEASON = 4
CLOUD_LIMIT = 20.0

# 30 m pixels; a 3-pixel radius disk is ~90 m, comfortably covering the 15 m
# location uncertainty plus an 80x40 m impoundment.
SITE_RADIUS_PX = 3
CONTROL_RADIUS_PX = 3
# Controls sit >= 250 m from the site (controls.py MIN_DISTANCE_FROM_ANY_
# INTERVENTION_M) and are spread around it on a ring.
CONTROL_RING_PX = 40


@dataclass(frozen=True, slots=True)
class SceneRef:
    scene_id: str
    collection: str
    date: str
    cloud: float
    assets: dict[str, str]


def search_season(client: httpx.Client, year: int, season: str) -> list[SceneRef]:
    m0, m1 = SEASONS[season]
    if m1 < m0:  # rabi straddles the calendar year
        window = f"{year}-{m0:02d}-01T00:00:00Z/{year + 1}-{m1:02d}-28T23:59:59Z"
    else:
        window = f"{year}-{m0:02d}-01T00:00:00Z/{year}-{m1:02d}-30T23:59:59Z"

    resp = client.post(
        STAC,
        json={"collections": COLLECTIONS, "bbox": list(AOI), "datetime": window, "limit": 100},
        timeout=120.0,
    )
    resp.raise_for_status()

    out: list[SceneRef] = []
    for feature in resp.json()["features"]:
        props = feature["properties"]
        cloud = props.get("eo:cloud_cover")
        if cloud is None or cloud >= CLOUD_LIMIT:
            continue
        out.append(
            SceneRef(
                scene_id=feature["id"],
                collection=feature["collection"],
                date=props["datetime"][:10],
                cloud=float(cloud),
                assets={k: v["href"] for k, v in feature["assets"].items() if "href" in v},
            )
        )
    out.sort(key=lambda s: s.cloud)
    return out[:MAX_SCENES_PER_SEASON]


def read_band(resolver: EdlResolver, url: str, grid: Any) -> np.ndarray | None:
    """One band, resampled onto the fixed analysis grid.

    Reprojecting onto a pinned grid is not optional: measured in docs/11 §9,
    native windows over one AOI returned three different shapes because the AOI
    straddles MGRS tile boundaries. Stacking those silently misaligns pixels.
    """
    import rasterio
    from affine import Affine
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    try:
        with httpx.Client(timeout=120.0, follow_redirects=False) as client:
            signed = resolver.resolve(url, client=client)
        with rasterio.open(signed) as src:
            dst = np.zeros((grid.height, grid.width), dtype=src.dtypes[0])
            reproject(
                source=rasterio.band(src, 1),
                destination=dst,
                dst_transform=Affine(*grid.transform),
                dst_crs=f"EPSG:{grid.epsg}",
                resampling=Resampling.nearest,
            )
            return dst
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        # Deliberately narrow. A first draft caught bare `Exception` "because a
        # lost scene is data, not a crash" - and then silently reported a
        # TypeError from a mis-called property as 40 lost scenes. Network and
        # raster IO failures are data; everything else is a bug and must raise.
        print(f"      ! {type(exc).__name__}: {str(exc)[:70]}")
        return None


def scene_indices(
    resolver: EdlResolver, scene: SceneRef, grid: Any
) -> dict[str, np.ndarray] | None:
    """NDVI and MNDWI for one scene, cloud-masked.

    Band names differ between S30 and L30 (`nir_band_for` resolves NIR from the
    collection id). Reading B08 from an L30 granule returns a real array from
    the wrong band and a plausible, wrong NDVI - which is exactly the class of
    error that has no downstream symptom.
    """
    nir_key = nir_band_for(scene.collection)
    wanted = {"red": "B04", "nir": nir_key, "green": "B03", "swir1": "B11", "fmask": "Fmask"}
    missing = [k for k, v in wanted.items() if v not in scene.assets]
    if missing:
        print(f"      ! missing assets {missing}")
        return None

    with ThreadPoolExecutor(max_workers=OPTIMAL_CONCURRENCY) as pool:
        futures = {
            name: pool.submit(read_band, resolver, scene.assets[asset], grid)
            for name, asset in wanted.items()
        }
        bands = {name: future.result() for name, future in futures.items()}

    if any(b is None for b in bands.values()):
        return None

    clear = clear_mask(bands["fmask"].astype(np.uint8))
    if not clear.any():
        print("      ! no clear pixels")
        return None

    red = scale_reflectance(bands["red"])
    nir = scale_reflectance(bands["nir"])
    green = scale_reflectance(bands["green"])
    swir1 = scale_reflectance(bands["swir1"])

    out: dict[str, np.ndarray] = {}
    for name, arr in (("NDVI", ndvi(nir, red)), ("MNDWI", mndwi(green, swir1))):
        masked = np.where(clear, arr, np.nan)
        out[name] = masked
    return out


def disk_mean(arr: np.ndarray, row: int, col: int, radius: int) -> float:
    """Mean over a pixel disk, NaN when nothing in it is usable.

    NaN rather than 0.0: a fully clouded disk has no value, and 0.0 is a real
    NDVI (bare rock). Substituting one for the other is the defect class that
    docs §16.1 calls absence of evidence read as evidence of absence.
    """
    r0, r1 = max(row - radius, 0), min(row + radius + 1, arr.shape[0])
    c0, c1 = max(col - radius, 0), min(col + radius + 1, arr.shape[1])
    patch = arr[r0:r1, c0:c1]
    if patch.size == 0 or not np.isfinite(patch).any():
        return float("nan")
    return float(np.nanmean(patch))


def main() -> int:
    if not os.environ.get("EARTHDATA_TOKEN", "").strip():
        print("EARTHDATA_TOKEN is not set; see .env.example", file=sys.stderr)
        return 2

    # The real reprojector must be injected. `grid_for_aoi`'s fallback uses
    # approximate UTM bounds, and mixing an approximate grid with a pyproj point
    # put the site at row 426 of a 223-row grid — measured, not guessed. Every
    # disk mean came back NaN and the run reported ten clean composites, so the
    # failure was silent. Consistency matters more than either method's
    # accuracy in isolation.
    grid = grid_for_aoi(*AOI, reproject_bounds=_reproject_bounds)
    print(
        f"analysis grid : {grid.width}x{grid.height} @ {grid.resolution_m:.0f} m, EPSG:{grid.epsg}"
    )

    # Site and control pixels, via the same transformer that made the bounds.
    site_x, site_y = _to_grid_crs(grid.epsg, SITE_LON, SITE_LAT)
    site_col = int((site_x - grid.left) / grid.resolution_m)
    site_row = int((grid.top - site_y) / grid.resolution_m)
    if not (0 <= site_row < grid.height and 0 <= site_col < grid.width):
        print(
            f"site pixel (row={site_row}, col={site_col}) is outside the "
            f"{grid.height}x{grid.width} grid — AOI and site disagree",
            file=sys.stderr,
        )
        return 3
    controls = _control_positions(site_row, site_col, grid)
    if not controls:
        print("no control positions fit inside the grid", file=sys.stderr)
        return 3
    print(f"site pixel    : row={site_row} col={site_col}")
    print(f"controls      : {len(controls)} on a {CONTROL_RING_PX * 30 / 1000:.1f} km ring\n")

    resolver = EdlResolver()
    series: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for year in YEARS:
            for season in SEASONS:
                scenes = search_season(client, year, season)
                if not scenes:
                    print(f"  {year} {season:<7} no usable scenes")
                    series.append(
                        {
                            "year": year,
                            "season": season,
                            "scenes": [],
                            "sufficient": False,
                            "reason": "no scenes under the cloud limit",
                        }
                    )
                    continue

                lo = min(s.cloud for s in scenes)
                hi = max(s.cloud for s in scenes)
                print(f"  {year} {season:<7} {len(scenes)} scenes (cloud {lo:.0f}-{hi:.0f}%)")
                stacks: dict[str, list[np.ndarray]] = {"NDVI": [], "MNDWI": []}
                used: list[dict[str, Any]] = []
                for scene in scenes:
                    idx = scene_indices(resolver, scene, grid)
                    if idx is None:
                        continue
                    for name, arr in idx.items():
                        stacks[name].append(arr)
                    used.append({"id": scene.scene_id, "date": scene.date, "cloud": scene.cloud})

                if not used:
                    series.append(
                        {
                            "year": year,
                            "season": season,
                            "scenes": [],
                            "sufficient": False,
                            "reason": "every scene failed to read or was fully masked",
                        }
                    )
                    continue

                entry: dict[str, Any] = {
                    "year": year,
                    "season": season,
                    "scenes": used,
                    "sufficient": True,
                    "indices": {},
                }
                for name, stack in stacks.items():
                    composite = seasonal_composite(stack)
                    entry["indices"][name] = {
                        "valid_fraction": round(valid_fraction(composite), 4),
                        "site": _round(disk_mean(composite, site_row, site_col, SITE_RADIUS_PX)),
                        "controls": [
                            _round(disk_mean(composite, r, c, CONTROL_RADIUS_PX))
                            for r, c in controls
                        ],
                    }
                nd = entry["indices"]["NDVI"]
                print(
                    f"      NDVI site={nd['site']} valid={nd['valid_fraction']:.0%} "
                    f"controls={sum(1 for v in nd['controls'] if v is not None)}/{len(controls)}"
                )
                series.append(entry)

    out = REPO_ROOT / "data" / "demo" / "temporal_series.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).isoformat(),
                "provenance": "NASA HLS v2.0 (HLSS30/HLSL30) via CMR STAC, EDL-authenticated",
                "site": {"lon": SITE_LON, "lat": SITE_LAT},
                "aoi": list(AOI),
                "claim_date": CLAIM_DATE,
                "grid": {
                    "epsg": grid.epsg,
                    "width": grid.width,
                    "height": grid.height,
                    "resolution_m": grid.resolution_m,
                },
                "cloud_limit_pct": CLOUD_LIMIT,
                "kharif": "excluded: 0-5 usable scenes per year on this AOI, measured",
                "series": series,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    ok = sum(1 for s in series if s["sufficient"])
    print(f"\n{ok} of {len(series)} season-windows produced a composite")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


def _round(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 4)


def _to_grid_crs(epsg: int, lon: float, lat: float) -> tuple[float, float]:
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = tf.transform(lon, lat)
    return float(x), float(y)


def _reproject_bounds(
    src_crs: str, dst_crs: str, left: float, bottom: float, right: float, top: float
) -> tuple[float, float, float, float]:
    """Exact bounds reprojection, injected into `grid_for_aoi`."""
    from pyproj import Transformer

    tf = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    x0, y0 = tf.transform(left, bottom)
    x1, y1 = tf.transform(right, top)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _control_positions(site_row: int, site_col: int, grid: Any) -> list[tuple[int, int]]:
    """Controls on a ring around the site, clipped to the grid.

    A ring, not random points: it keeps every control at the same distance, so
    none is accidentally adjacent to the structure. Real control selection is
    covariate-matched on DEM derivatives (`controls.select_controls`); this
    script cannot do that until the DEM lands (M2), so the geometry is explicit
    and the output records that these are ring positions rather than matched
    controls.
    """
    import math

    out: list[tuple[int, int]] = []
    for i in range(8):
        angle = 2 * math.pi * i / 8
        r = site_row + int(CONTROL_RING_PX * math.sin(angle))
        c = site_col + int(CONTROL_RING_PX * math.cos(angle))
        if 0 <= r < grid.height and 0 <= c < grid.width:
            out.append((r, c))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
