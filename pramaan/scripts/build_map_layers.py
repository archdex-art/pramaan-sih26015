#!/usr/bin/env python3
"""Vectorise the DEM derivatives into map layers for the plan view.

## Why this exists

The problem statement asks, in its own words, for *"land use maps, drainage maps,
watershed intervention maps and spatial change detection products"*. Until now
the console had no map, on the deliberate grounds that a convincing-looking
empty map is the most dishonest thing this product could ship.

That objection no longer holds. The drainage network here is **extracted from
the six mosaicked NASADEM tiles by the same WhiteboxTools chain that produced
the terrain verdict**, at the same calibrated stream-initiation threshold. The
intervention pin is the claim's own coordinate. The control pins are the twelve
sites `select_controls` actually chose. Every geometry on the map is one the
analysis used.

## Method

Drainage is traced by following the **D8 pointer** from every stream cell to its
downstream neighbour, which yields a connected network with flow direction and
Strahler order per segment. Contour-tracing the stream raster instead would give
prettier curves and lose the topology — and topology is the part that matters,
since Strahler order is what the terrain rule tests.

Output is WGS84 GeoJSON-shaped, clipped to the AOI plus a margin so the site has
visible drainage context without shipping a district of geometry to a browser.

    uv run --with rasterio --with numpy --with pyproj python scripts/build_map_layers.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEM_DIR = REPO_ROOT / "data" / "demo" / "dem"
TERRAIN = REPO_ROOT / "data" / "demo" / "terrain.json"
OUT = REPO_ROOT / "data" / "demo" / "map_layers.json"

#: Analysis AOI, and the margin drawn around it. The margin exists so the site's
#: drainage arrives from somewhere visible rather than beginning at the frame
#: edge — a channel that starts at the border reads as an artefact.
AOI = (77.02, 19.02, 77.08, 19.08)
MARGIN_DEG = 0.022

#: WhiteboxTools D8 encoding: (row delta, col delta) per pointer value.
D8: dict[int, tuple[int, int]] = {
    1: (0, 1),
    2: (1, 1),
    4: (1, 0),
    8: (1, -1),
    16: (0, -1),
    32: (-1, -1),
    64: (-1, 0),
    128: (-1, 1),
}


def main() -> int:
    import rasterio
    from pyproj import Transformer

    for name in ("streams.tif", "strahler.tif", "d8_pointer.tif"):
        if not (DEM_DIR / name).is_file():
            print(
                f"{name} missing — run `make terrain` first (it needs the "
                "NASADEM .hgt tiles, which are gitignored)",
                file=sys.stderr,
            )
            return 2
    if not TERRAIN.is_file():
        print(f"{TERRAIN} missing — run `make terrain`", file=sys.stderr)
        return 2

    terrain = json.loads(TERRAIN.read_text(encoding="utf-8"))
    window = (
        AOI[0] - MARGIN_DEG,
        AOI[1] - MARGIN_DEG,
        AOI[2] + MARGIN_DEG,
        AOI[3] + MARGIN_DEG,
    )

    with rasterio.open(DEM_DIR / "streams.tif") as src:
        epsg = src.crs.to_epsg()
        to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        to_wgs = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
        x0, y0 = to_native.transform(window[0], window[1])
        x1, y1 = to_native.transform(window[2], window[3])
        win = (
            rasterio.windows.from_bounds(
                min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), transform=src.transform
            )
            .round_offsets()
            .round_lengths()
        )
        streams = src.read(1, window=win)
        transform = src.window_transform(win)
        nodata = src.nodata

    with rasterio.open(DEM_DIR / "strahler.tif") as src:
        order = src.read(1, window=win)
    with rasterio.open(DEM_DIR / "d8_pointer.tif") as src:
        pointer = src.read(1, window=win)

    is_stream = (streams > 0) & (streams != nodata)
    rows, cols = np.nonzero(is_stream)
    print(f"window {streams.shape[1]}x{streams.shape[0]} px · {rows.size} stream cells")

    # One segment per stream cell, from its centre to its downstream neighbour's.
    # Segments rather than merged polylines: the renderer weights each by its own
    # Strahler order, and merging would force a single width per chain.
    segments: list[dict[str, object]] = []
    a, b, c, d, e, f = transform.a, transform.b, transform.c, transform.d, transform.e, transform.f

    def centre(r: int, col: int) -> tuple[float, float]:
        x = c + a * (col + 0.5) + b * (r + 0.5)
        y = f + d * (col + 0.5) + e * (r + 0.5)
        return to_wgs.transform(x, y)

    h, w = streams.shape
    for r, col in zip(rows, cols, strict=True):
        ptr = int(pointer[r, col])
        step = D8.get(ptr)
        if step is None:
            continue  # outlet cell, or nodata pointer
        nr, nc = r + step[0], col + step[1]
        if not (0 <= nr < h and 0 <= nc < w):
            continue
        o = order[r, col]
        segments.append(
            {
                "from": [round(v, 6) for v in centre(int(r), int(col))],
                "to": [round(v, 6) for v in centre(int(nr), int(nc))],
                # Strahler order drives line weight. Clamped at 1 because a
                # stream cell with an undefined order is still a channel.
                "order": int(o) if o > 0 else 1,
            }
        )

    orders = [int(s["order"]) for s in segments]  # type: ignore[arg-type]
    print(f"drainage segments: {len(segments)}  Strahler {min(orders)}–{max(orders)}")

    controls = [
        {
            "control_id": ctrl["control_id"],
            "lonlat": [round(v, 6) for v in to_wgs.transform(ctrl["x"], ctrl["y"])],
            "slope_deg": ctrl["slope_deg"],
            "elevation_m": ctrl["elevation_m"],
            "dist_to_stream_m": ctrl["dist_to_stream_m"],
            "dist_from_site_m": ctrl["dist_from_site_m"],
        }
        for ctrl in terrain["matched_controls"]
    ]

    site = terrain["site"]
    payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "crs": "EPSG:4326",
        "source_epsg": epsg,
        "aoi": list(AOI),
        "window": list(window),
        "provenance": {
            "drainage": (
                f"D8 network extracted from {terrain['dem']['source']} at the "
                f"calibrated threshold of "
                f"{terrain['threshold_calibration']['chosen_threshold_px']:.0f} px "
                f"({terrain['threshold_calibration']['chosen_density_km_per_km2']:.2f} "
                "km/km²). Same chain and threshold that produced the terrain verdict."
            ),
            "controls": (
                f"{len(controls)} sites selected by controls.select_controls from "
                f"{terrain['control_selection']['n_candidates']} candidates on "
                "DEM-derived slope, elevation and distance-to-stream."
            ),
            "site": "The claim's own recorded coordinate.",
            "basemap": (
                "None. Drawn as a survey plan on the product's own ground so the "
                "console works with the network interface disabled (§38)."
            ),
        },
        "site": {
            "lonlat": [site["lon"], site["lat"]],
            "strahler_order": site["strahler_order"]["median"],
            "dist_to_stream_m": site["dist_to_stream_m"]["median"],
            "slope_deg": site["slope_deg"]["median"],
        },
        "controls": controls,
        "drainage": segments,
    }
    OUT.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)} ({OUT.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
