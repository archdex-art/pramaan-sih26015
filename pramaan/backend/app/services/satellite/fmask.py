"""HLS v2.0 Fmask decoding and AOI-specific usable-fraction accounting.

## Bit layout — documented AND empirically validated

HLS v2.0 packs per-pixel quality into one uint8 band produced by Fmask 4.7, with
a 150 m dilation around clouds and shadows labelled "adjacent to cloud/shadow".

| Bit | Meaning |
|-----|---------|
| 0 | Cirrus (reserved, not used in v2.0) |
| 1 | Cloud |
| 2 | Adjacent to cloud/shadow (150 m dilation) |
| 3 | Cloud shadow |
| 4 | Snow / ice |
| 5 | Water |
| 6-7 | Aerosol level: 00 climatology, 01 low, 10 moderate, 11 high |

Validated against real granules on 2026-08-28 rather than trusted: for five
scenes spanning 15-86 % reported `eo:cloud_cover`, the bit-1 fraction correlates
**r = +0.9969** with the reported figure (mean absolute difference 5.3 pp).

## The finding that changes how data sufficiency must be computed

Masking bit 1 alone is not enough, and scene metadata is not a substitute for
the mask. Measured, same five scenes:

| Reported cloud | bit 1 | +bit 2 (adjacent) | +bit 3 (shadow) | combined |
|---|---|---|---|---|
| 15 % | 10.4 % | 10.9 % | 5.1 % | **24.7 %** |
| 38 % | 34.4 % | 4.5 % | 4.1 % | 42.3 % |
| 45 % | 37.8 % | 13.8 % | 8.9 % | 57.6 % |
| 71 % | 63.1 % | 9.6 % | 9.9 % | 79.6 % |
| 86 % | 83.0 % | 3.7 % | 4.3 % | 89.9 % |

A scene advertised as **15 % cloudy loses 24.7 % of its pixels** once adjacency
and shadow are honoured. So `eo:cloud_cover` is systematically optimistic by
roughly 10 percentage points as a usability estimate.

Two consequences, both already required by the design and now quantified:

1. `data_sufficiency` must be derived from the Fmask over the **claim's AOI**,
   never from scene-level `eo:cloud_cover` (docs §15.2 Stage 3: "a scene 40%
   cloudy ... is useless if the 40% is over our AOI"). Metadata filtering is a
   cheap pre-filter only.
2. Scene *selection* thresholds tuned against `eo:cloud_cover` will admit
   scenes that turn out unusable over the AOI. Filter on metadata, then verify
   on the mask, and record both.

## The aerosol trap — why bits 6-7 are NOT used as a filter

HLS documentation recommends avoiding pixels flagged high aerosol (bits 6-7 ==
11). Following that advice over the demo region **discards essentially all
data**. Measured over the Marathwada AOI:

| Collection | Date | Meta cloud | aerosol==11 | aerosol==00 | cloud-clear |
|---|---|---|---|---|---|
| HLSS30 | 2023-01-01 | 1 % | 97.0 % | 0.0 % | 94.4 % |
| HLSS30 | 2023-05-11 | 2 % | 90.2 % | 0.0 % | 96.0 % |
| HLSS30 | 2024-11-06 | 3 % | 99.7 % | 0.0 % | 92.6 % |
| HLSL30 | 2023-01-02 | 7 % | 99.9 % | 0.0 % | 83.9 % |
| HLSL30 | 2024-11-04 | 11 % | 99.9 % | 0.0 % | 84.3 % |

The flag is saturated across both sensors, three seasons and two years, and
`aerosol == 00` never occurs. On one 3 %-cloud scene every Fmask value in the
AOI was >= 192, i.e. bits 6-7 always set, and usable fraction went from
**98.89 % to 0.00 %** with the exclusion enabled.

So `DEFAULT_EXCLUDE_HIGH_AEROSOL = False`.

This is the most dangerous class of bug this project can have, which is why the
default is pinned by a test. With the exclusion on, the satellite family would
be unavailable for **every** claim, the engine would dutifully return
`N1 INCONCLUSIVE` with a correct-looking "insufficient data" reason, and the
system would appear to be working conservatively while actually being blind.
Nobody would notice until somebody asked why nothing is ever corroborated.

## Compositing requires a common analysis grid

Reading a native window per scene does NOT produce stackable arrays. Measured on
four rabi scenes over one AOI: shapes came back as (305, 335), (1119, 335) and
(1119, 1059), because the AOI straddles MGRS tile boundaries and each granule
covers a different part of it.

`indices.seasonal_composite` refuses a mismatched stack rather than broadcasting
something plausible. The satellite worker must therefore resample every scene
onto a **fixed per-AOI analysis grid** (the district's UTM zone, a pinned
transform and shape) before compositing — not read native windows and stack.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

#: HLS fill value for the Fmask band.
FMASK_FILL = 255

BIT_CIRRUS = 1 << 0  # reserved, unused in v2.0
BIT_CLOUD = 1 << 1
BIT_ADJACENT = 1 << 2
BIT_CLOUD_SHADOW = 1 << 3
BIT_SNOW_ICE = 1 << 4
BIT_WATER = 1 << 5
AEROSOL_SHIFT = 6
AEROSOL_MASK = 0b11 << AEROSOL_SHIFT
AEROSOL_HIGH = 0b11

#: Default exclusions. Bit 2 is included deliberately: the measured cost is
#: 3.7-13.8 pp of pixels, and the alternative is admitting pixels within 150 m
#: of a cloud edge into an index whose whole purpose is detecting a change of a
#: few hundredths. Cheap insurance against a spurious NDVI delta.
DEFAULT_EXCLUDE = BIT_CLOUD | BIT_ADJACENT | BIT_CLOUD_SHADOW | BIT_SNOW_ICE

#: DO NOT exclude high-aerosol pixels by default. See the module docstring's
#: "aerosol trap" section: over the demo region the flag is set on 90-99.9% of
#: pixels in every scene tested, so excluding it discards entire granules while
#: the genuine cloud-clear fraction is 84-96%.
DEFAULT_EXCLUDE_HIGH_AEROSOL = False


@dataclass(frozen=True, slots=True)
class MaskStats:
    """Per-AOI accounting for one scene, for the lineage record."""

    total_px: int
    fill_px: int
    cloud_px: int
    adjacent_px: int
    shadow_px: int
    snow_px: int
    water_px: int
    high_aerosol_px: int
    usable_px: int

    @property
    def usable_fraction(self) -> float:
        """Usable pixels as a fraction of NON-FILL pixels.

        Denominator excludes fill deliberately: fill means the AOI extends
        past the granule footprint, which is a tiling artefact, not a cloud
        problem. Counting it as unusable would make a perfectly clear scene look
        marginal purely because the AOI straddles a tile edge.
        """
        observed = self.total_px - self.fill_px
        return self.usable_px / observed if observed > 0 else 0.0

    @property
    def fill_fraction(self) -> float:
        return self.fill_px / self.total_px if self.total_px > 0 else 0.0

    @property
    def cloud_fraction(self) -> float:
        observed = self.total_px - self.fill_px
        return self.cloud_px / observed if observed > 0 else 0.0

    def lineage(self) -> dict[str, object]:
        return {
            "total_px": self.total_px,
            "fill_px": self.fill_px,
            "usable_px": self.usable_px,
            "usable_fraction": round(self.usable_fraction, 4),
            "cloud_fraction": round(self.cloud_fraction, 4),
            "fill_fraction": round(self.fill_fraction, 4),
            "cloud_px": self.cloud_px,
            "adjacent_px": self.adjacent_px,
            "shadow_px": self.shadow_px,
            "snow_px": self.snow_px,
            "water_px": self.water_px,
            "high_aerosol_px": self.high_aerosol_px,
        }


def clear_mask(
    fmask: npt.NDArray[np.uint8],
    exclude: int = DEFAULT_EXCLUDE,
    *,
    exclude_high_aerosol: bool = DEFAULT_EXCLUDE_HIGH_AEROSOL,
) -> npt.NDArray[np.bool_]:
    """Boolean mask of usable pixels: True = keep.

    `exclude_high_aerosol` drops pixels where bits 6-7 == 11. It defaults to
    **False**, against HLS's general recommendation, because over the demo
    region that flag is set on 90-99.9% of pixels in every scene tested — see
    the aerosol trap in the module docstring. Enabling it discards whole
    granules. Left as an argument so a region where the flag is informative can
    switch it on.
    """
    if fmask.dtype != np.uint8:
        raise TypeError(f"Fmask must be uint8, got {fmask.dtype}")
    not_fill = fmask != FMASK_FILL
    clear = (fmask & exclude) == 0
    if exclude_high_aerosol:
        clear &= ((fmask & AEROSOL_MASK) >> AEROSOL_SHIFT) != AEROSOL_HIGH
    return not_fill & clear


def water_mask(fmask: npt.NDArray[np.uint8]) -> npt.NDArray[np.bool_]:
    """Fmask's own water flag (bit 5).

    Used only as a cross-check on our MNDWI/Otsu water extraction, never as the
    primary water source: it is a by-product of cloud detection, not a
    calibrated water product. Disagreement between the two is informative and
    belongs in the dissent panel.
    """
    return ((fmask & BIT_WATER) != 0) & (fmask != FMASK_FILL)


def mask_stats(
    fmask: npt.NDArray[np.uint8],
    exclude: int = DEFAULT_EXCLUDE,
    *,
    exclude_high_aerosol: bool = DEFAULT_EXCLUDE_HIGH_AEROSOL,
) -> MaskStats:
    """Full per-AOI accounting. This is what feeds `data_sufficiency`."""
    if fmask.dtype != np.uint8:
        raise TypeError(f"Fmask must be uint8, got {fmask.dtype}")
    total = int(fmask.size)
    fill = int((fmask == FMASK_FILL).sum())
    observed = fmask[fmask != FMASK_FILL]
    aerosol = (observed & AEROSOL_MASK) >> AEROSOL_SHIFT
    usable = int(clear_mask(fmask, exclude, exclude_high_aerosol=exclude_high_aerosol).sum())
    return MaskStats(
        total_px=total,
        fill_px=fill,
        cloud_px=int((observed & BIT_CLOUD != 0).sum()),
        adjacent_px=int((observed & BIT_ADJACENT != 0).sum()),
        shadow_px=int((observed & BIT_CLOUD_SHADOW != 0).sum()),
        snow_px=int((observed & BIT_SNOW_ICE != 0).sum()),
        water_px=int((observed & BIT_WATER != 0).sum()),
        high_aerosol_px=int((aerosol == AEROSOL_HIGH).sum()),
        usable_px=usable,
    )
