"""Tests for Fmask decoding and the published index formulas.

Two things are being protected:

1. **The bit layout**, because it was validated empirically against real HLS
   granules (r = +0.9969 against reported cloud cover) and a "tidying" edit that
   shifted a constant would be undetectable without a test.
2. **The formulas as published in docs §15.3**, computed on hand-checkable
   values. If a formula changes, `INDEX_FORMULA_VERSION` must change with it,
   because stored verdicts carry that version in their lineage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.satellite import fmask as fm  # noqa: E402
from app.services.satellite import indices as ix  # noqa: E402

# --- Fmask bit layout -----------------------------------------------------


def test_bit_constants_match_the_validated_hls_layout() -> None:
    """Empirically validated on real granules — do not renumber."""
    assert fm.BIT_CIRRUS == 0b00000001
    assert fm.BIT_CLOUD == 0b00000010
    assert fm.BIT_ADJACENT == 0b00000100
    assert fm.BIT_CLOUD_SHADOW == 0b00001000
    assert fm.BIT_SNOW_ICE == 0b00010000
    assert fm.BIT_WATER == 0b00100000
    assert fm.AEROSOL_MASK == 0b11000000
    assert fm.FMASK_FILL == 255


def test_default_exclusion_includes_the_adjacency_dilation() -> None:
    """Bit 2 costs 3.7-13.8 pp of pixels and is worth it.

    Admitting pixels within 150 m of a cloud edge into an index whose purpose is
    detecting a change of a few hundredths is a false-signal generator.
    """
    assert fm.DEFAULT_EXCLUDE & fm.BIT_ADJACENT
    assert fm.DEFAULT_EXCLUDE & fm.BIT_CLOUD
    assert fm.DEFAULT_EXCLUDE & fm.BIT_CLOUD_SHADOW
    assert fm.DEFAULT_EXCLUDE & fm.BIT_SNOW_ICE
    # Water is NOT an exclusion: water is signal, not noise, for this product.
    assert not fm.DEFAULT_EXCLUDE & fm.BIT_WATER


def test_clear_mask_keeps_only_unflagged_pixels() -> None:
    arr = np.array(
        [
            0,  # clear
            fm.BIT_CLOUD,
            fm.BIT_ADJACENT,
            fm.BIT_CLOUD_SHADOW,
            fm.BIT_SNOW_ICE,
            fm.BIT_WATER,  # water is kept
            fm.FMASK_FILL,
        ],
        dtype=np.uint8,
    )
    keep = fm.clear_mask(arr)
    assert keep.tolist() == [True, False, False, False, False, True, False]


def test_high_aerosol_is_NOT_excluded_by_default() -> None:
    """The aerosol trap, pinned. See fmask module docstring.

    HLS recommends dropping high-aerosol pixels. Over the demo region that flag
    is set on 90-99.9% of pixels in every scene tested (both sensors, three
    seasons, two years) and `aerosol == 00` never occurs. On one 3%-cloud scene,
    enabling the exclusion took usable fraction from 98.89% to 0.00%.

    This test exists because the failure mode is invisible: with the exclusion
    on, the satellite family would be unavailable for EVERY claim, the engine
    would return a correct-looking `N1 INCONCLUSIVE` citing insufficient data,
    and the system would look conservative while being blind.
    """
    high = np.array([0b11000000], dtype=np.uint8)
    assert fm.DEFAULT_EXCLUDE_HIGH_AEROSOL is False
    assert fm.clear_mask(high)[0], "high-aerosol pixels must be KEPT by default"
    # Still available for regions where the flag carries information.
    assert not fm.clear_mask(high, exclude_high_aerosol=True)[0]


def test_aerosol_exclusion_when_enabled_spares_lower_levels() -> None:
    moderate = np.array([0b10000000], dtype=np.uint8)
    low = np.array([0b01000000], dtype=np.uint8)
    assert fm.clear_mask(moderate, exclude_high_aerosol=True)[0]
    assert fm.clear_mask(low, exclude_high_aerosol=True)[0]


def test_a_saturated_aerosol_scene_stays_usable() -> None:
    """Regression on the measured scene: all values >= 192, 98.89% usable."""
    # 192 = clear + aerosol 11; 194 = cloud + aerosol 11
    arr = np.array([192] * 99 + [194], dtype=np.uint8)
    s = fm.mask_stats(arr)
    assert s.usable_fraction == pytest.approx(0.99)
    assert fm.mask_stats(arr, exclude_high_aerosol=True).usable_fraction == 0.0


def test_water_mask_reads_bit_5_and_respects_fill() -> None:
    arr = np.array([fm.BIT_WATER, 0, fm.FMASK_FILL], dtype=np.uint8)
    assert fm.water_mask(arr).tolist() == [True, False, False]


def test_fmask_rejects_wrong_dtype() -> None:
    """A uint16 array would make every bit test silently wrong."""
    with pytest.raises(TypeError, match="must be uint8"):
        fm.clear_mask(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(TypeError, match="must be uint8"):
        fm.mask_stats(np.zeros((2, 2), dtype=np.uint16))


# --- usable fraction accounting ------------------------------------------


def test_usable_fraction_excludes_fill_from_the_denominator() -> None:
    """Fill is a tile-edge artefact, not a cloud problem.

    An AOI straddling a granule edge must not look marginal on a clear day.
    """
    arr = np.array([0, 0, 0, fm.FMASK_FILL, fm.FMASK_FILL], dtype=np.uint8)
    s = fm.mask_stats(arr)
    assert s.total_px == 5
    assert s.fill_px == 2
    assert s.usable_px == 3
    assert s.usable_fraction == 1.0, "3 clear of 3 observed is fully usable"
    assert s.fill_fraction == pytest.approx(0.4)


def test_usable_fraction_of_an_entirely_filled_aoi_is_zero_not_nan() -> None:
    arr = np.full(4, fm.FMASK_FILL, dtype=np.uint8)
    s = fm.mask_stats(arr)
    assert s.usable_fraction == 0.0
    assert s.cloud_fraction == 0.0


def test_mask_stats_counts_each_flag_independently() -> None:
    """A pixel can be cloud AND shadow-adjacent; counts must not be exclusive."""
    both = fm.BIT_CLOUD | fm.BIT_ADJACENT
    arr = np.array([both, fm.BIT_CLOUD, 0], dtype=np.uint8)
    s = fm.mask_stats(arr)
    assert s.cloud_px == 2
    assert s.adjacent_px == 1
    assert s.usable_px == 1


def test_mask_stats_reproduces_the_measured_pattern() -> None:
    """Regression on the real finding: a '15% cloud' scene loses ~25% of pixels.

    Synthesised to the measured proportions (10.4% cloud, 10.9% adjacent,
    5.1% shadow, non-overlapping) to pin the arithmetic that produced the
    24.7% figure in the fmask module docstring.
    """
    n = 1000
    arr = np.zeros(n, dtype=np.uint8)
    arr[:104] = fm.BIT_CLOUD
    arr[104:213] = fm.BIT_ADJACENT
    arr[213:264] = fm.BIT_CLOUD_SHADOW
    s = fm.mask_stats(arr)
    assert s.cloud_px == 104
    # 264 flagged -> 736 usable -> 73.6% usable, i.e. 26.4% lost from a scene
    # whose metadata advertised 15% cloud.
    assert s.usable_fraction == pytest.approx(0.736)
    assert s.cloud_fraction == pytest.approx(0.104)
    assert s.usable_fraction < 1 - s.cloud_fraction, (
        "honouring adjacency and shadow must cost MORE than the cloud bit alone "
        "— this is why data_sufficiency cannot come from eo:cloud_cover"
    )


def test_mask_stats_lineage_is_complete() -> None:
    s = fm.mask_stats(np.array([0, fm.BIT_CLOUD], dtype=np.uint8))
    lin = s.lineage()
    for key in ("usable_fraction", "cloud_fraction", "fill_fraction", "usable_px"):
        assert key in lin, key


# --- band resolution ------------------------------------------------------


def test_nir_band_differs_between_s30_and_l30() -> None:
    """The trap: reading B08 from an L30 granule returns the wrong band silently."""
    assert ix.nir_band_for("HLSS30_2.0") == "B08"
    assert ix.nir_band_for("HLSL30_2.0") == "B05"
    assert ix.nir_band_for("hlss30") == "B08"


def test_unknown_collection_refuses_to_guess_the_nir_band() -> None:
    with pytest.raises(ValueError, match="Refusing to guess"):
        ix.nir_band_for("SENTINEL-2-L2A")


# --- reflectance scaling --------------------------------------------------


def test_reflectance_scaling_and_fill_to_nan() -> None:
    raw = np.array([2000, 5000, ix.REFLECTANCE_FILL], dtype=np.int16)
    out = ix.scale_reflectance(raw)
    assert out[0] == pytest.approx(0.2)
    assert out[1] == pytest.approx(0.5)
    assert np.isnan(out[2]), "fill must become NaN, not 0.0"


def test_fill_as_nan_not_zero_matters_for_ndvi() -> None:
    """Reflectance 0 gives NDVI 0, a legitimate value for bare rock.

    So a fill pixel read as 0 would be invisible. NaN propagates instead.
    """
    fill = ix.scale_reflectance(np.array([ix.REFLECTANCE_FILL], dtype=np.int16))
    assert np.isnan(ix.ndvi(fill, fill)[0])


# --- index formulas, hand-checkable --------------------------------------


def test_ndvi_matches_the_published_formula() -> None:
    nir = np.array([0.4], dtype=np.float32)
    red = np.array([0.1], dtype=np.float32)
    assert ix.ndvi(nir, red)[0] == pytest.approx((0.4 - 0.1) / (0.4 + 0.1))


def test_mndwi_matches_the_published_formula() -> None:
    green = np.array([0.30], dtype=np.float32)
    swir1 = np.array([0.10], dtype=np.float32)
    assert ix.mndwi(green, swir1)[0] == pytest.approx(0.5)


def test_ndwi_and_mndwi_differ_in_their_second_band() -> None:
    """Both are published; conflating them would be a silent substitution."""
    green = np.array([0.3], dtype=np.float32)
    nir = np.array([0.4], dtype=np.float32)
    swir1 = np.array([0.1], dtype=np.float32)
    assert ix.ndwi(green, nir)[0] != pytest.approx(ix.mndwi(green, swir1)[0])


def test_bsi_matches_the_published_formula() -> None:
    blue = np.array([0.10], dtype=np.float32)
    red = np.array([0.20], dtype=np.float32)
    nir = np.array([0.30], dtype=np.float32)
    swir1 = np.array([0.40], dtype=np.float32)
    expected = ((0.40 + 0.20) - (0.30 + 0.10)) / ((0.40 + 0.20) + (0.30 + 0.10))
    assert ix.bsi(blue, red, nir, swir1)[0] == pytest.approx(expected)


def test_savi_reduces_to_scaled_ndvi_shape_and_honours_l() -> None:
    nir = np.array([0.4], dtype=np.float32)
    red = np.array([0.1], dtype=np.float32)
    expected = ((0.4 - 0.1) / (0.4 + 0.1 + 0.5)) * 1.5
    assert ix.savi(nir, red)[0] == pytest.approx(expected)
    # Different L gives a different answer: L is not decorative.
    assert ix.savi(nir, red, 0.25)[0] != pytest.approx(ix.savi(nir, red, 0.5)[0])


def test_savi_rejects_an_out_of_range_soil_factor() -> None:
    with pytest.raises(ValueError, match=r"L must be in \[0,1\]"):
        ix.savi(np.array([0.4]), np.array([0.1]), 1.5)


def test_ndmi_matches_the_published_formula() -> None:
    nir = np.array([0.4], dtype=np.float32)
    swir1 = np.array([0.2], dtype=np.float32)
    assert ix.ndmi(nir, swir1)[0] == pytest.approx((0.4 - 0.2) / (0.4 + 0.2))


@pytest.mark.parametrize(
    "fn,args",
    [
        (ix.ndvi, 2),
        (ix.ndwi, 2),
        (ix.mndwi, 2),
        (ix.ndmi, 2),
    ],
)
def test_normalised_indices_stay_in_range(fn, args: int) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(3)
    a = rng.uniform(0, 1, 5000).astype(np.float32)
    b = rng.uniform(0, 1, 5000).astype(np.float32)
    out = fn(a, b)
    finite = out[np.isfinite(out)]
    assert finite.min() >= -1.0 - 1e-6
    assert finite.max() <= 1.0 + 1e-6


def test_zero_sum_band_pair_yields_nan_not_a_division_error() -> None:
    zeros = np.zeros(3, dtype=np.float32)
    out = ix.ndvi(zeros, zeros)
    assert np.isnan(out).all()


def test_formula_registry_covers_every_implemented_index() -> None:
    assert set(ix.INDEX_FORMULAE) == {"NDVI", "SAVI", "NDWI", "MNDWI", "NDMI", "BSI"}
    assert ix.INDEX_FORMULA_VERSION == "idx-v1"


# --- masking and compositing ---------------------------------------------


def test_apply_mask_nans_out_flagged_pixels() -> None:
    index = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    clear = np.array([True, False, True])
    out = ix.apply_mask(index, clear)
    assert out[0] == pytest.approx(0.5)
    assert np.isnan(out[1])
    assert not np.shares_memory(out, index), "must not mutate the caller's array"


def test_apply_mask_rejects_a_shape_mismatch() -> None:
    """A mismatch means bands were read at different windows — silent misalignment."""
    with pytest.raises(ValueError, match="shape mismatch|!="):
        ix.apply_mask(np.zeros((4, 4), dtype=np.float32), np.ones((4, 5), dtype=bool))


def test_seasonal_composite_is_a_median_robust_to_one_bad_scene() -> None:
    """The residual failure mode after Fmask is an undetected cloud edge."""
    good = np.array([[0.50]], dtype=np.float32)
    outlier = np.array([[0.95]], dtype=np.float32)
    out = ix.seasonal_composite([good, good, outlier])
    assert out[0, 0] == pytest.approx(0.50), "median must reject the outlier"
    mean = np.mean([0.50, 0.50, 0.95])
    assert out[0, 0] != pytest.approx(mean)


def test_seasonal_composite_ignores_nan_pixels() -> None:
    a = np.array([[0.4]], dtype=np.float32)
    b = np.array([[np.nan]], dtype=np.float32)
    assert ix.seasonal_composite([a, b])[0, 0] == pytest.approx(0.4)


def test_all_nan_pixel_composites_to_nan() -> None:
    nan = np.array([[np.nan]], dtype=np.float32)
    assert np.isnan(ix.seasonal_composite([nan, nan])[0, 0])


def test_composite_rejects_empty_and_mismatched_stacks() -> None:
    with pytest.raises(ValueError, match="empty scene stack"):
        ix.seasonal_composite([])
    with pytest.raises(ValueError, match="share one shape"):
        ix.seasonal_composite(
            [np.zeros((2, 2), dtype=np.float32), np.zeros((3, 3), dtype=np.float32)]
        )


def test_valid_fraction_and_zonal_stats_agree() -> None:
    index = np.array([0.1, 0.2, np.nan, 0.3], dtype=np.float32)
    assert ix.valid_fraction(index) == pytest.approx(0.75)
    stats = ix.zonal_stats(index)
    assert stats["valid_fraction"] == pytest.approx(0.75)
    assert stats["n_valid"] == 3
    assert stats["min"] == pytest.approx(0.1)
    assert stats["max"] == pytest.approx(0.3)
    assert stats["median"] == pytest.approx(0.2)


def test_zonal_stats_of_an_all_nan_aoi_is_nan_with_zero_valid() -> None:
    """A fully clouded AOI must report NaN and 0.0, never a fabricated value."""
    stats = ix.zonal_stats(np.full(4, np.nan, dtype=np.float32))
    assert np.isnan(stats["median"])
    assert stats["valid_fraction"] == 0.0
    assert stats["n_valid"] == 0


def test_valid_fraction_of_empty_array_is_zero() -> None:
    assert ix.valid_fraction(np.array([], dtype=np.float32)) == 0.0
