"""Fixed per-AOI analysis grid.

## Why this module exists

Measured during the docs/11 §9 validation: reading a native window per scene
returned shapes `(305, 335)`, `(1119, 335)` and `(1119, 1059)` for one AOI across
four scenes, because the AOI straddles MGRS tile boundaries and each granule
covers a different part of it.

`indices.seasonal_composite` refuses a mismatched stack rather than broadcasting
something plausible, so that surfaced as an exception. The fix is upstream: every
scene is resampled onto **one grid, pinned per AOI**, before anything is
composited or compared.

## Why the grid must be pinned, not derived per call

A grid computed from whichever scenes happened to be available would drift as
scenes are added. Two consequences, both silent:

* A PRE composite and a POST composite built months apart would sit on different
  grids, and their difference would compare adjacent-but-not-identical ground.
* A verdict could not be recomputed byte-identically from its lineage (docs
  §21.3), because the grid is an input and it would not have been recorded.

So `AnalysisGrid` is a value object, it goes into the verdict's lineage, and
`from_bounds` is deterministic given the same AOI and resolution.

## CRS policy

Storage is EPSG:4326 (geography), all measurement is in a projected CRS. The grid
picks the UTM zone from the AOI centroid via `utm_epsg_for`, which is the single
enforcement point the design calls for. India spans UTM 42N-47N; a district never
straddles more than two zones, and the centroid rule makes the choice
reproducible rather than a judgement call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: India's UTM zones run 42N (west of 72E) through 47N (east of 96E). Outside
#: that range we refuse rather than silently projecting into the wrong zone.
_INDIA_LON_MIN = 66.0
_INDIA_LON_MAX = 98.0

#: The 30 m tier the problem statement names.
DEFAULT_RESOLUTION_M = 30.0


class OutsideSupportedRegion(ValueError):
    """Raised for an AOI outside the longitudes PRAMAAN is configured for."""


def utm_zone_for(longitude: float) -> int:
    """UTM zone number for a longitude. Zone 1 starts at 180W, 6 degrees wide."""
    return int((longitude + 180.0) // 6.0) + 1


def utm_epsg_for(longitude: float, latitude: float) -> int:
    """EPSG code for the UTM zone containing this point.

    Northern hemisphere is 326xx, southern 327xx. India is entirely northern, but
    the southern branch is implemented rather than assumed away — a hardcoded
    326xx would be a latent bug the moment anybody points this at another
    country.
    """
    if not _INDIA_LON_MIN <= longitude <= _INDIA_LON_MAX:
        raise OutsideSupportedRegion(
            f"longitude {longitude} is outside the configured range "
            f"[{_INDIA_LON_MIN}, {_INDIA_LON_MAX}]. Refusing to pick a UTM zone: "
            "projecting into the wrong zone distorts every distance and area in "
            "the analysis, and nothing downstream would flag it."
        )
    zone = utm_zone_for(longitude)
    return (32600 if latitude >= 0 else 32700) + zone


@dataclass(frozen=True, slots=True)
class AnalysisGrid:
    """A pinned raster grid for one AOI. Hashable, and part of the lineage."""

    epsg: int
    #: Projected bounds, metres, snapped to the resolution.
    left: float
    bottom: float
    right: float
    top: float
    resolution_m: float
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.resolution_m <= 0:
            raise ValueError(f"resolution_m must be positive: {self.resolution_m}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"grid has non-positive extent ({self.width}x{self.height}): the "
                "AOI is smaller than one pixel at this resolution"
            )
        if self.right <= self.left or self.top <= self.bottom:
            raise ValueError("grid bounds are inverted or degenerate")

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) — numpy order, so it can be compared to an array."""
        return (self.height, self.width)

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """Affine coefficients (a, b, c, d, e, f) in rasterio order.

        Returned as a plain tuple rather than an `affine.Affine` so this module
        stays free of a raster dependency and can be unit-tested without GDAL.
        The caller builds `Affine(*grid.transform)`.
        """
        return (self.resolution_m, 0.0, self.left, 0.0, -self.resolution_m, self.top)

    @property
    def pixel_area_m2(self) -> float:
        return self.resolution_m * self.resolution_m

    @property
    def n_pixels(self) -> int:
        return self.width * self.height

    def area_km2(self) -> float:
        return self.n_pixels * self.pixel_area_m2 / 1e6

    def lineage(self) -> dict[str, object]:
        """Everything needed to reconstruct this grid exactly (docs §21.3)."""
        return {
            "epsg": self.epsg,
            "left": self.left,
            "bottom": self.bottom,
            "right": self.right,
            "top": self.top,
            "resolution_m": self.resolution_m,
            "width": self.width,
            "height": self.height,
        }

    def matches(self, shape: tuple[int, int]) -> bool:
        return shape == self.shape


def snap_bounds(
    left: float, bottom: float, right: float, top: float, resolution_m: float
) -> tuple[float, float, float, float]:
    """Expand bounds outward to whole multiples of the resolution.

    Snapping outward, never inward: shrinking the AOI to fit the grid would
    silently drop the edge of a command buffer. Snapping to absolute multiples of
    the resolution (rather than relative to the AOI corner) means two overlapping
    AOIs in the same UTM zone share pixel edges, so their statistics are directly
    comparable — which matters when a control site's AOI overlaps the site's.
    """
    return (
        math.floor(left / resolution_m) * resolution_m,
        math.floor(bottom / resolution_m) * resolution_m,
        math.ceil(right / resolution_m) * resolution_m,
        math.ceil(top / resolution_m) * resolution_m,
    )


def grid_for_projected_bounds(
    left: float,
    bottom: float,
    right: float,
    top: float,
    epsg: int,
    *,
    resolution_m: float = DEFAULT_RESOLUTION_M,
) -> AnalysisGrid:
    """Build a pinned grid from bounds already in the target projected CRS."""
    sl, sb, sr, st = snap_bounds(left, bottom, right, top, resolution_m)
    return AnalysisGrid(
        epsg=epsg,
        left=sl,
        bottom=sb,
        right=sr,
        top=st,
        resolution_m=resolution_m,
        width=int(round((sr - sl) / resolution_m)),
        height=int(round((st - sb) / resolution_m)),
    )


def grid_for_aoi(
    lon_min: float,
    lat_min: float,
    lon_max: float,
    lat_max: float,
    *,
    resolution_m: float = DEFAULT_RESOLUTION_M,
    reproject_bounds: object = None,
) -> AnalysisGrid:
    """Build a pinned grid for a geographic AOI.

    `reproject_bounds` is injected rather than imported so this module needs no
    raster dependency: pass `rasterio.warp.transform_bounds`. When omitted, a
    small local approximation is used, which is adequate for choosing a grid (the
    snapping tolerates metre-level differences) but is documented as an
    approximation rather than presented as a projection.
    """
    if lon_max <= lon_min or lat_max <= lat_min:
        raise ValueError(
            f"AOI bounds inverted or degenerate: ({lon_min}, {lat_min}, {lon_max}, {lat_max})"
        )
    centre_lon = (lon_min + lon_max) / 2.0
    centre_lat = (lat_min + lat_max) / 2.0
    epsg = utm_epsg_for(centre_lon, centre_lat)

    if reproject_bounds is not None:
        left, bottom, right, top = reproject_bounds(  # type: ignore[operator]
            "EPSG:4326", f"EPSG:{epsg}", lon_min, lat_min, lon_max, lat_max
        )
    else:
        left, bottom, right, top = _approximate_utm_bounds(lon_min, lat_min, lon_max, lat_max, epsg)
    return grid_for_projected_bounds(left, bottom, right, top, epsg, resolution_m=resolution_m)


def _approximate_utm_bounds(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float, epsg: int
) -> tuple[float, float, float, float]:
    """Local planar approximation of UTM bounds. Adequate for grid selection.

    Not a projection: it assumes a locally flat earth around the AOI centre. For
    a district-sized AOI the error is tens of metres, which the outward snapping
    absorbs. Any measurement — areas, distances, zonal statistics — must use a
    real projection, never this.
    """
    zone = epsg % 100
    centre_meridian = (zone - 1) * 6.0 - 180.0 + 3.0
    lat_mid = math.radians((lat_min + lat_max) / 2.0)
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * math.cos(lat_mid)
    false_easting = 500_000.0
    return (
        false_easting + (lon_min - centre_meridian) * m_per_deg_lon,
        lat_min * m_per_deg_lat,
        false_easting + (lon_max - centre_meridian) * m_per_deg_lon,
        lat_max * m_per_deg_lat,
    )
