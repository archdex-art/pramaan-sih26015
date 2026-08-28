"""Spectral index definitions — the formulas published in docs §15.3.

Pure functions of masked reflectance arrays. No IO, no band-name guessing: the
caller supplies named arrays, because a silent band mix-up between sensors is
the single easiest way to produce a confidently wrong index, and the harmonised
HLS band numbering differs from Landsat's.

## Band mapping (HLS harmonises both sensors onto Sentinel-2 naming)

| Role | HLS S30 / L30 | Landsat 8/9 native |
|---|---|---|
| Blue | B02 | B2 |
| Green | B03 | B3 |
| Red | B04 | B4 |
| NIR | B08 (S30) / B05 (L30) | B5 |
| SWIR1 | B11 | B6 |
| SWIR2 | B12 | B7 |

The NIR asymmetry between S30 (`B08`) and L30 (`B05`) is a real trap: reading
`B08` from an L30 granule silently returns the wrong band. `nir_band_for()`
resolves it from the collection id rather than leaving it to a caller's memory.

## Cross-sensor policy (the W4 fix)

An index time series may only mix sensors **within a harmonised product family**
— HLS L30 + S30 are co-registered and BRDF-normalised for exactly this. A
Resourcesat/LISS-III series is maintained separately and never concatenated into
an HLS trend. This module does not enforce that (it sees arrays, not
provenance); the satellite worker does, and the UI shows a sensor-family chip.
"""

from __future__ import annotations

import warnings

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.floating]

#: HLS surface reflectance is stored as int16 scaled by 10,000.
HLS_SCALE = 1.0 / 10_000.0

#: HLS fill for reflectance bands.
REFLECTANCE_FILL = -9999

#: Guards division where a band pair sums to zero (both bands zero/masked).
_EPS = 1e-6

#: SAVI's soil-adjustment constant. A fixed assumption, flagged as such in
#: docs §15.3, and exposed as an argument so it is never silently hardcoded.
SAVI_L_DEFAULT = 0.5


def nir_band_for(collection: str) -> str:
    """Resolve the NIR asset key for an HLS collection.

    S30 (Sentinel-2 derived) carries NIR as B08; L30 (Landsat derived) as B05.
    Getting this wrong returns a real array from a real band and produces a
    plausible-looking, wrong NDVI — a failure with no exception to catch it.
    """
    c = collection.upper()
    if "S30" in c:
        return "B08"
    if "L30" in c:
        return "B05"
    raise ValueError(
        f"unknown HLS collection {collection!r}: expected an id containing "
        "'S30' or 'L30'. Refusing to guess the NIR band."
    )


def scale_reflectance(raw: npt.NDArray[np.integer]) -> Array:
    """int16 DN -> float reflectance, with fill turned into NaN.

    NaN rather than 0.0 deliberately: a fill pixel read as reflectance 0 gives
    NDVI = 0, which is a *legitimate* value for bare rock, so the error would be
    invisible. NaN propagates and is caught by the valid-pixel accounting.
    """
    out = raw.astype(np.float32) * HLS_SCALE
    out[raw == REFLECTANCE_FILL] = np.nan
    return out


def _normalised_difference(a: Array, b: Array) -> Array:
    """(a - b) / (a + b), NaN-safe and zero-safe."""
    denom = a + b
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (a - b) / np.where(np.abs(denom) < _EPS, np.nan, denom)
    return np.asarray(out, dtype=np.float32)


def ndvi(nir: Array, red: Array) -> Array:
    """(NIR - Red)/(NIR + Red). Vegetation vigour and cover.

    Limitation, printed in every report: saturates in dense canopy and is
    sensitive to soil background where cover is sparse — which is most of
    semi-arid Marathwada outside the kharif season. Prefer SAVI there.
    """
    return _normalised_difference(nir, red)


def savi(nir: Array, red: Array, soil_factor: float = SAVI_L_DEFAULT) -> Array:
    """((NIR-Red)/(NIR+Red+L))*(1+L). Vegetation under sparse cover.

    `L` is a fixed assumption (0.5), not a fitted parameter. Stated as such.
    """
    if not 0.0 <= soil_factor <= 1.0:
        raise ValueError(f"SAVI L must be in [0,1], got {soil_factor}")
    denom = nir + red + soil_factor
    with np.errstate(invalid="ignore", divide="ignore"):
        out = ((nir - red) / np.where(np.abs(denom) < _EPS, np.nan, denom)) * (1.0 + soil_factor)
    return np.asarray(out, dtype=np.float32)


def ndwi(green: Array, nir: Array) -> Array:
    """McFeeters (Green - NIR)/(Green + NIR).

    The index the WDC-PMKSY guidelines name explicitly, which is why it is
    computed and reported even though MNDWI is preferred for extraction.
    Limitation: confuses built-up surfaces with water.
    """
    return _normalised_difference(green, nir)


def mndwi(green: Array, swir1: Array) -> Array:
    """Xu (Green - SWIR1)/(Green + SWIR1). Preferred for water extraction.

    Better built-up suppression than NDWI. Still struggles with turbid or
    vegetation-choked shallow water — common in small Indian farm ponds, and a
    stated limitation rather than a hidden one.
    """
    return _normalised_difference(green, swir1)


def ndmi(nir: Array, swir1: Array) -> Array:
    """(NIR - SWIR1)/(NIR + SWIR1). Moisture PROXY.

    Not soil moisture. Labelled a proxy everywhere it surfaces.
    """
    return _normalised_difference(nir, swir1)


def bsi(blue: Array, red: Array, nir: Array, swir1: Array) -> Array:
    """((SWIR1+Red)-(NIR+Blue))/((SWIR1+Red)+(NIR+Blue)). Bare soil.

    Confounded by the crop calendar: a harvested field and a degraded field look
    alike. Only interpretable against a same-season baseline, which is why the
    temporal design never compares across seasons.
    """
    return _normalised_difference(swir1 + red, nir + blue)


#: Registry so the worker and the API can enumerate what exists without a
#: hardcoded list drifting from the implementations.
INDEX_FORMULAE: dict[str, str] = {
    "NDVI": "(NIR - Red) / (NIR + Red)",
    "SAVI": "((NIR - Red) / (NIR + Red + L)) * (1 + L), L=0.5",
    "NDWI": "(Green - NIR) / (Green + NIR)",
    "MNDWI": "(Green - SWIR1) / (Green + SWIR1)",
    "NDMI": "(NIR - SWIR1) / (NIR + SWIR1)",
    "BSI": "((SWIR1 + Red) - (NIR + Blue)) / ((SWIR1 + Red) + (NIR + Blue))",
}

#: Version stamped into every verdict's lineage. A change to any formula above
#: bumps this, so a stored verdict can never be silently compared against a
#: value computed under different maths (docs §21.3).
INDEX_FORMULA_VERSION = "idx-v1"


def apply_mask(index: Array, clear: npt.NDArray[np.bool_]) -> Array:
    """Set non-clear pixels to NaN.

    Masking after computing, rather than before, keeps the arithmetic simple and
    costs nothing: NaN propagates through the normalised differences anyway.
    """
    if index.shape != clear.shape:
        raise ValueError(
            f"index shape {index.shape} != mask shape {clear.shape}; a shape "
            "mismatch here means bands were read at different windows or "
            "resolutions, which would silently misalign the AOI"
        )
    out = np.array(index, dtype=np.float32, copy=True)
    out[~clear] = np.nan
    return out


def seasonal_composite(stack: list[Array]) -> Array:
    """Per-pixel median across a season's masked scenes.

    Median, not mean: it is robust to a single undetected cloud edge, which is
    exactly the residual failure mode after Fmask. With 2-3 usable scenes the
    median is weak, which is why `data_sufficiency` travels alongside every
    composite rather than being folded into it.
    """
    if not stack:
        raise ValueError("cannot composite an empty scene stack")
    shapes = {a.shape for a in stack}
    if len(shapes) != 1:
        raise ValueError(f"all scenes must share one shape, got {sorted(shapes)}")
    arr = np.stack(stack, axis=0)
    with warnings.catch_warnings():
        # A pixel clouded in EVERY scene of the season legitimately composites
        # to NaN — that is the signal `data_sufficiency` is built to carry, not
        # an error. numpy warns anyway, so the warning is suppressed here rather
        # than left to pollute every caller's logs during a district ingest.
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        out = np.nanmedian(arr, axis=0)
    return np.asarray(out, dtype=np.float32)


def valid_fraction(index: Array) -> float:
    """Fraction of finite pixels. The composite's own data-sufficiency term."""
    if index.size == 0:
        return 0.0
    return float(np.isfinite(index).sum() / index.size)


def zonal_stats(index: Array) -> dict[str, float]:
    """Min/median/max/mean plus valid fraction over an AOI.

    Returns the same min/median/max shape the terrain producer uses, so the
    engine sees one consistent notion of "a variable summarised over an AOI"
    regardless of which family produced it.
    """
    finite = index[np.isfinite(index)]
    if finite.size == 0:
        return {
            "min": float("nan"),
            "median": float("nan"),
            "max": float("nan"),
            "mean": float("nan"),
            "valid_fraction": 0.0,
            "n_valid": 0.0,
        }
    return {
        "min": float(finite.min()),
        "median": float(np.median(finite)),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
        "valid_fraction": float(finite.size / index.size),
        "n_valid": float(finite.size),
    }
