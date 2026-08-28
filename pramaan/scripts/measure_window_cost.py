#!/usr/bin/env python3
"""Measure what a windowed COG read actually costs, per AOI scale.

## Why this script exists

`docs/11-feasibility.md` §7 recorded "windowed read = 8.9 % of tile" and sized
the imagery budget from it. That measurement was taken on a *site-sized* AOI and
then applied to a plan that builds **whole-district** composites. Those are not
the same number, and the difference decides whether the download budget is 2 GB
or 15 GB per district.

Rather than estimate, this reads the COG's own internal tile index and computes
the exact compressed byte count for the tiles a given window intersects. No
pixel data is transferred: indexing one band costs about 2 KiB.

## Method

A COG stores level-0 pixels as independently compressed tiles (256x256 for HLS)
with a byte count per tile in the TIFF directory. A windowed read fetches
exactly the tiles the window touches. So:

    cost(window) = sum(TileByteCounts[t] for t in tiles intersecting window)

This is exact, not sampled, and it is why the numbers below have no error bar.

## The finding

Cost tracks the **spatial footprint of the claim set**, not the district
boundary, because a tile fetched once serves every claim inside it. Scoping the
demo to a few sub-watersheds is ~8x cheaper than district-wide composites while
analysing the same claims.

Run:  uv run --with httpx --with tifffile --with rasterio python scripts/measure_window_cost.py
Needs EARTHDATA_TOKEN (see .env.example).
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.satellite.edl_auth import EdlResolver  # noqa: E402

# A granule already used in the validated index chain (docs/11 §9), so this
# measurement is comparable with the numbers recorded there.
GRANULE = (
    "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSS30.020/"
    "HLS.S30.T43QGB.2024311T052011.v2.0/HLS.S30.T43QGB.2024311T052011.v2.0.B04.tif"
)

# Windows to price, smallest first. Sizes are the AOI the analysis actually
# needs at each scale, not arbitrary boxes:
#   site        - uncertainty disk (max 15 m) + the 300 m command buffer
#   micro-ws    - the unit claims are grouped in
#   sub-ws      - the unit matched controls must come from (controls.py)
#   block/district - what a "district composite" would actually read
AOIS: tuple[tuple[str, float, float, float, float], ...] = (
    ("site + 300 m command buffer", 77.04, 19.04, 77.06, 19.06),
    ("micro-watershed", 77.00, 19.00, 77.10, 19.10),
    ("sub-watershed (control unit)", 76.90, 18.90, 77.20, 19.20),
    ("block", 76.50, 18.50, 77.50, 19.50),
    ("district (Nanded extent)", 76.931, 18.263, 78.365, 19.925),
)

# Budget arithmetic, from measured inputs:
#   6.4-year span (windows.py: 24 months/side + 3-month buffer, and a claim
#   needs 2 full years post-construction) x 3 seasons = 19 season-windows
#   6 usable scenes per season (docs/11 §8, rabi 75 % usable)
#   7 bands (4 indices need B03,B04,B05,B06,B08,B11 + Fmask)
SEASON_WINDOWS = 19
SCENES_PER_SEASON = 6
BANDS = 7
BAND_READS = SEASON_WINDOWS * SCENES_PER_SEASON * BANDS


class RangeFile(io.RawIOBase):
    """Read-only file over HTTP range requests, counting bytes fetched.

    Size comes from a ranged GET, never HEAD. An AWS presigned URL is signed
    for one specific HTTP method, so a HEAD against a GET-presign returns 403
    Forbidden with a signature error that looks like an auth failure. That trap
    cost a debugging cycle; it is why `EdlResolver` has no size probe.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.pos = 0
        self.bytes_read = 0
        self.requests = 0
        self._client = httpx.Client(timeout=120.0, follow_redirects=True)
        first = self._client.get(url, headers={"Range": "bytes=0-0"})
        first.raise_for_status()
        self.size = int(first.headers["content-range"].split("/")[1])
        self.bytes_read += len(first.content)
        self.requests += 1

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self) -> int:
        return self.pos

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
        if self.pos >= self.size:
            return 0
        end = min(self.pos + len(buffer) - 1, self.size - 1)
        resp = self._client.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"})
        resp.raise_for_status()
        data = resp.content
        self.requests += 1
        self.bytes_read += len(data)
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)

    def close(self) -> None:
        self._client.close()
        super().close()


@dataclass(frozen=True)
class TileIndex:
    """The COG's level-0 tile grid and per-tile compressed byte counts."""

    width: int
    height: int
    tile_w: int
    tile_h: int
    byte_counts: tuple[int, ...]
    band_bytes_on_wire: int
    index_bytes_fetched: int
    index_requests: int

    @property
    def tiles_across(self) -> int:
        return math.ceil(self.width / self.tile_w)

    @property
    def level0_bytes(self) -> int:
        return sum(self.byte_counts)


def read_tile_index(signed_url: str) -> TileIndex:
    import tifffile

    handle = RangeFile(signed_url)
    try:
        with tifffile.TiffFile(handle) as tif:
            page = tif.pages[0]
            return TileIndex(
                width=int(page.imagewidth),
                height=int(page.imagelength),
                tile_w=int(page.tilewidth),
                tile_h=int(page.tilelength),
                byte_counts=tuple(int(b) for b in page.databytecounts),
                band_bytes_on_wire=handle.size,
                index_bytes_fetched=handle.bytes_read,
                index_requests=handle.requests,
            )
    finally:
        handle.close()


def window_cost(
    index: TileIndex,
    bounds_native: tuple[float, float, float, float],
    tile_bounds: tuple[float, float, float, float],
    res: float,
) -> tuple[int, int, int, int]:
    """Exact bytes for the tiles a window intersects.

    Returns (tiles_touched, bytes, window_px_w, window_px_h). A window entirely
    outside the granule returns zeros rather than raising: a district straddles
    several MGRS tiles and legitimately misses some of them.
    """
    t_left, t_bottom, t_right, t_top = tile_bounds
    left = max(bounds_native[0], t_left)
    bottom = max(bounds_native[1], t_bottom)
    right = min(bounds_native[2], t_right)
    top = min(bounds_native[3], t_top)
    if left >= right or bottom >= top:
        return (0, 0, 0, 0)

    col0 = int((left - t_left) // res)
    col1 = int((right - t_left - 1e-6) // res)
    row0 = int((t_top - top) // res)
    row1 = int((t_top - bottom - 1e-6) // res)

    tx0, tx1 = col0 // index.tile_w, col1 // index.tile_w
    ty0, ty1 = row0 // index.tile_h, row1 // index.tile_h
    across = index.tiles_across
    ids = [ty * across + tx for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    total = sum(index.byte_counts[i] for i in ids if 0 <= i < len(index.byte_counts))
    return (len(ids), total, col1 - col0 + 1, row1 - row0 + 1)


def main() -> int:
    if not os.environ.get("EARTHDATA_TOKEN", "").strip():
        print("EARTHDATA_TOKEN is not set; see .env.example", file=sys.stderr)
        return 2

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    import rasterio
    from rasterio.warp import transform_bounds

    with httpx.Client(timeout=120.0, follow_redirects=False) as client:
        signed = EdlResolver().resolve(GRANULE, client=client)

    with rasterio.open(signed) as src:
        crs = src.crs
        res = float(src.transform.a)
        tile_bounds = (
            float(src.bounds.left),
            float(src.bounds.bottom),
            float(src.bounds.right),
            float(src.bounds.top),
        )

    index = read_tile_index(signed)
    band_mb = index.band_bytes_on_wire / 1e6

    print(f"granule            : {GRANULE.rsplit('/', 1)[-1]}")
    print(f"grid               : {index.width}x{index.height} @ {res:.0f} m, {crs}")
    print(
        f"footprint          : {(tile_bounds[2] - tile_bounds[0]) / 1000:.0f} x "
        f"{(tile_bounds[3] - tile_bounds[1]) / 1000:.0f} km"
    )
    print(f"band on wire       : {band_mb:.2f} MB")
    print(
        f"level-0 pixel data : {index.level0_bytes / 1e6:.2f} MB "
        f"in {len(index.byte_counts)} tiles of {index.tile_w}x{index.tile_h}"
    )
    print(
        f"index cost         : {index.index_bytes_fetched / 1024:.1f} KiB "
        f"in {index.index_requests} range requests (no pixels fetched)\n"
    )

    header = (
        f"{'AOI scale':<30}{'window px':>13}{'tiles':>8}{'MB':>9}{'% band':>9}{'GB budget':>11}"
    )
    print(header)
    print("-" * len(header))

    results = []
    for label, lon_min, lat_min, lon_max, lat_max in AOIS:
        native = transform_bounds("EPSG:4326", crs, lon_min, lat_min, lon_max, lat_max)
        tiles, nbytes, pw, ph = window_cost(index, native, tile_bounds, res)
        if tiles == 0:
            print(f"{label:<30}{'no overlap':>13}")
            continue
        mb = nbytes / 1e6
        budget_gb = BAND_READS * mb / 1000
        print(
            f"{label:<30}{f'{pw}x{ph}':>13}{tiles:>8}{mb:>9.2f}"
            f"{100 * nbytes / index.band_bytes_on_wire:>8.1f}%{budget_gb:>11.1f}"
        )
        results.append(
            {
                "aoi": label,
                "window_px": [pw, ph],
                "tiles": tiles,
                "bytes": nbytes,
                "fraction_of_band": round(nbytes / index.band_bytes_on_wire, 4),
                "budget_gb": round(budget_gb, 2),
            }
        )
    print("-" * len(header))
    print(
        f"GB budget = {BAND_READS} band-reads "
        f"({SEASON_WINDOWS} season-windows x {SCENES_PER_SEASON} usable scenes "
        f"x {BANDS} bands), 6.4-year span"
    )

    out = REPO_ROOT / "docs" / "12-window-cost.log.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).isoformat(),
                "granule": GRANULE,
                "band_bytes_on_wire": index.band_bytes_on_wire,
                "level0_bytes": index.level0_bytes,
                "tile_shape": [index.tile_w, index.tile_h],
                "grid": [index.width, index.height],
                "band_reads_assumed": BAND_READS,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
