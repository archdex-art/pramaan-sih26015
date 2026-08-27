"""Terrain producer contracts.

These are the value objects the terrain worker hands to the reconciliation
engine. They are deliberately plain data with no raster handles: the worker does
the sampling (rasterio, IO, slow), and everything downstream — the plausibility
rules, the detectability gate — is a pure function of these values.

That split is what makes the terrain rules unit-testable without a DEM, and it
is why golden case 21 can exercise an implausible farm-pond siting with no
imagery on disk at all.

## The unit hazard

The design document's §18.1 rule table mixes slope units between rows:

    check_dam         "slope < 5 deg"
    percolation_tank  "slope < 5%"
    earthen_bund      "slope 1-15%"
    contour_trench    "slope 5-33%"

5 degrees and 5 percent differ by nearly a factor of two (5% is 2.86 deg). A
single `slope` float with an implied unit would silently mis-gate half the
intervention types. So: this module stores slope in **degrees only**, the rule
table declares its unit explicitly per threshold, and conversion happens in one
place (`SlopeThreshold.as_degrees`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

SlopeUnit = Literal["degrees", "percent"]


def percent_to_degrees(slope_percent: float) -> float:
    """Convert a rise/run percentage to degrees. 5% -> 2.862 deg."""
    return math.degrees(math.atan(slope_percent / 100.0))


def degrees_to_percent(slope_degrees: float) -> float:
    return math.tan(math.radians(slope_degrees)) * 100.0


@dataclass(frozen=True, slots=True)
class DiskStat:
    """A terrain variable summarised over the location uncertainty disk.

    Every terrain variable is a distribution, not a value, because the claim's
    coordinate is only known to within its GPS accuracy (docs §16.2 STEP 2). A
    single-pixel sample would be the most common way to produce a confidently
    wrong terrain verdict.
    """

    minimum: float
    median: float
    maximum: float

    def __post_init__(self) -> None:
        if not self.minimum <= self.median <= self.maximum:
            raise ValueError(
                f"disk stat is not ordered: min={self.minimum} "
                f"median={self.median} max={self.maximum}"
            )


@dataclass(frozen=True, slots=True)
class TerrainSample:
    """Terrain variables sampled over a claim's uncertainty disk.

    Produced by the terrain worker from the district's DEM derivatives; consumed
    by `plausibility.evaluate()`.
    """

    #: Radius actually used, in metres: max(gps_accuracy, 15).
    disk_radius_m: float
    slope_deg: DiskStat
    #: Strahler stream order. 0 means "not on an extracted channel".
    strahler_order: DiskStat
    #: Flow accumulation in pixels.
    flow_accumulation_px: DiskStat
    #: Distance to the nearest extracted stream, in metres.
    dist_to_stream_m: DiskStat
    #: Contributing upstream area in km^2.
    upstream_area_km2: DiskStat
    #: Whether the disk sits in a closed/filled depression (from the DEM's
    #: pre-breach depression mask). None when the mask was unavailable.
    in_depression: bool | None = None

    # --- Lineage: what produced these numbers (docs §21.3) -----------------
    dem_product: str = "unknown"
    dem_version: str = "unknown"
    #: The calibrated flow-accumulation stream-initiation threshold used for
    #: this district, and its agreement score against the reference drainage
    #: layer. This is the W3 fix: "we calibrated a threshold and here is the
    #: score", not "we picked a threshold".
    stream_threshold_px: float | None = None
    stream_threshold_agreement: float | None = None

    def lineage(self) -> dict[str, object]:
        return {
            "dem_product": self.dem_product,
            "dem_version": self.dem_version,
            "disk_radius_m": self.disk_radius_m,
            "stream_threshold_px": self.stream_threshold_px,
            "stream_threshold_agreement": self.stream_threshold_agreement,
        }


def uncertainty_disk_radius_m(gps_accuracy_m: float | None, floor_m: float = 15.0) -> float:
    """r = max(gps_accuracy, 15 m) — docs §16.2 STEP 2.

    The 15 m floor exists because a reported accuracy better than half a 30 m
    pixel is not meaningful for sampling a 30 m raster, and because DRISHTI's
    own capture guidance accepts up to 10 m. A missing accuracy is treated as
    the floor rather than as zero: absent metadata must never produce a
    *tighter* sampling window than present metadata.
    """
    if gps_accuracy_m is None:
        return floor_m
    if gps_accuracy_m < 0:
        raise ValueError(f"gps_accuracy_m cannot be negative: {gps_accuracy_m}")
    return max(gps_accuracy_m, floor_m)
