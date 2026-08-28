"""Tests for the fixed analysis grid and the reproducibility guarantee.

The grid closes Trap 2 from docs/11 §9: native windows are not stackable, so
everything is resampled onto one pinned grid per AOI.

The reproducibility digest turns docs §21.3's promise — *"a verdict can be
recomputed byte-identically from its lineage"* — from a claim into something a
test can fail on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import all_agreeing, bundle, fam, gates  # noqa: E402

from app.services.audit import (  # noqa: E402
    bundle_digest,
    canonical_json,
    compare_verdicts,
    verdict_digest,
)
from app.services.reconcile import EngineConfig, reconcile  # noqa: E402
from app.services.satellite import grid as gd  # noqa: E402

# --- UTM zone selection --------------------------------------------------


def test_utm_zone_for_indian_longitudes() -> None:
    assert gd.utm_zone_for(77.0) == 43  # Marathwada, Delhi
    assert gd.utm_zone_for(72.9) == 43  # Mumbai
    assert gd.utm_zone_for(88.4) == 45  # Kolkata
    assert gd.utm_zone_for(94.0) == 46


def test_utm_epsg_is_northern_for_india() -> None:
    assert gd.utm_epsg_for(77.0, 19.0) == 32643
    assert gd.utm_epsg_for(88.4, 22.6) == 32645


def test_southern_hemisphere_branch_exists() -> None:
    """Not reachable for India, but a hardcoded 326xx would be a latent bug."""
    assert gd.utm_epsg_for(77.0, -19.0) == 32743


def test_longitude_outside_the_configured_region_is_refused() -> None:
    """Projecting into the wrong zone distorts every distance and nothing flags it."""
    with pytest.raises(gd.OutsideSupportedRegion, match="Refusing to pick a UTM zone"):
        gd.utm_epsg_for(2.35, 48.85)  # Paris


# --- snapping ------------------------------------------------------------


def test_bounds_snap_outward_never_inward() -> None:
    """Shrinking the AOI would silently drop the edge of a command buffer."""
    left, bottom, right, top = gd.snap_bounds(1001.0, 2001.0, 3001.0, 4001.0, 30.0)
    assert left <= 1001.0
    assert bottom <= 2001.0
    assert right >= 3001.0
    assert top >= 4001.0


def test_snapping_is_to_absolute_multiples_so_overlapping_aois_share_edges() -> None:
    """A control AOI overlapping the site's must share pixel edges, or their
    statistics are not directly comparable."""
    a = gd.grid_for_projected_bounds(1000.0, 2000.0, 4000.0, 5000.0, 32643)
    b = gd.grid_for_projected_bounds(1600.0, 2600.0, 4600.0, 5600.0, 32643)
    assert a.left % 30.0 == 0
    assert b.left % 30.0 == 0
    assert (b.left - a.left) % 30.0 == 0, "grids must be on a shared lattice"


# --- grid properties -----------------------------------------------------


def test_grid_shape_is_numpy_order() -> None:
    """So it can be compared directly against an array's .shape."""
    g = gd.grid_for_projected_bounds(0.0, 0.0, 300.0, 600.0, 32643)
    assert g.width == 10
    assert g.height == 20
    assert g.shape == (20, 10)
    assert g.matches((20, 10))
    assert not g.matches((10, 20))


def test_transform_is_rasterio_order_with_negative_y() -> None:
    g = gd.grid_for_projected_bounds(1000.0, 2000.0, 1300.0, 2600.0, 32643)
    a, b, c, d, e, f = g.transform
    assert a == 30.0
    assert e == -30.0, "north-up rasters have a negative y resolution"
    assert (c, f) == (g.left, g.top)
    assert b == 0.0 and d == 0.0


def test_pixel_area_matches_the_detectability_gate() -> None:
    """900 m2 at 30 m — the same figure the gate compares footprints against."""
    g = gd.grid_for_projected_bounds(0.0, 0.0, 300.0, 300.0, 32643)
    assert g.pixel_area_m2 == 900.0

    from app.services.terrain.detectability import PIXEL_AREA_30M_M2

    assert g.pixel_area_m2 == PIXEL_AREA_30M_M2


def test_area_and_pixel_count() -> None:
    g = gd.grid_for_projected_bounds(0.0, 0.0, 3000.0, 3000.0, 32643)
    assert g.n_pixels == 100 * 100
    assert g.area_km2() == pytest.approx(9.0)


def test_degenerate_grids_are_refused() -> None:
    with pytest.raises(ValueError, match="non-positive extent"):
        gd.AnalysisGrid(32643, 0.0, 0.0, 10.0, 10.0, 30.0, 0, 5)
    with pytest.raises(ValueError, match="resolution_m must be positive"):
        gd.AnalysisGrid(32643, 0.0, 0.0, 300.0, 300.0, 0.0, 10, 10)
    with pytest.raises(ValueError, match="inverted or degenerate"):
        gd.AnalysisGrid(32643, 300.0, 0.0, 0.0, 300.0, 30.0, 10, 10)


def test_inverted_aoi_is_refused() -> None:
    with pytest.raises(ValueError, match="inverted or degenerate"):
        gd.grid_for_aoi(77.2, 19.2, 76.9, 18.9)


# --- determinism, which is what makes the grid part of the lineage -------


def test_grid_is_deterministic_for_the_same_aoi() -> None:
    """A grid derived per-call from available scenes would drift as scenes land.

    Two consequences, both silent: PRE and POST composites built months apart
    would sit on different grids, and a verdict could not be recomputed from its
    lineage because the grid is an input that was never recorded.
    """
    a = gd.grid_for_aoi(76.90, 18.90, 77.20, 19.20)
    b = gd.grid_for_aoi(76.90, 18.90, 77.20, 19.20)
    assert a == b
    assert a.lineage() == b.lineage()
    assert hash(a) == hash(b)


def test_grid_lineage_is_sufficient_to_reconstruct_it() -> None:
    g = gd.grid_for_aoi(76.90, 18.90, 77.20, 19.20)
    lin = g.lineage()
    rebuilt = gd.AnalysisGrid(
        epsg=int(lin["epsg"]),  # type: ignore[arg-type]
        left=float(lin["left"]),  # type: ignore[arg-type]
        bottom=float(lin["bottom"]),  # type: ignore[arg-type]
        right=float(lin["right"]),  # type: ignore[arg-type]
        top=float(lin["top"]),  # type: ignore[arg-type]
        resolution_m=float(lin["resolution_m"]),  # type: ignore[arg-type]
        width=int(lin["width"]),  # type: ignore[arg-type]
        height=int(lin["height"]),  # type: ignore[arg-type]
    )
    assert rebuilt == g


def test_demo_aoi_grid_is_the_size_measured_against_real_data() -> None:
    """docs/11 §9 validated a 1065x1119 grid at 30 m for this AOI.

    The approximation used here is planar, so an exact match is not expected;
    what matters is that it lands within a few percent of the real projection,
    because the whole point is that every scene shares ONE grid.
    """
    g = gd.grid_for_aoi(76.90, 18.90, 77.20, 19.20)
    assert g.epsg == 32643
    assert 1000 < g.width < 1150
    assert 1050 < g.height < 1200


def test_injected_reprojector_is_used_when_provided() -> None:
    calls: list[tuple] = []

    def fake(src, dst, *bounds):  # type: ignore[no-untyped-def]
        calls.append((src, dst, bounds))
        return (300000.0, 2000000.0, 330000.0, 2030000.0)

    g = gd.grid_for_aoi(76.9, 18.9, 77.2, 19.2, reproject_bounds=fake)
    assert calls, "the injected reprojector must be used, not bypassed"
    assert calls[0][1] == "EPSG:32643"

    # The injected bounds are 300000..330000 easting (a clean 1000 pixels) and
    # 2000000..2030000 northing. 2000000 is NOT a multiple of 30, so the grid
    # snaps outward to 1999980..2030010 and gains a row: 1001, not 1000. That
    # is the intended behaviour — snapping inward would clip the AOI edge.
    assert g.width == 1000
    assert g.height == 1001
    assert g.bottom == 1999980.0
    assert g.top == 2030010.0
    assert g.bottom <= 2000000.0 and g.top >= 2030000.0


# --- reproducibility: canonicalisation ----------------------------------


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_normalises_float_representation() -> None:
    """Last-bit noise must not change a digest."""
    assert canonical_json({"x": 0.1 + 0.2}) == canonical_json({"x": 0.30000000000000004})


def test_canonical_json_keeps_bool_distinct_from_int() -> None:
    """bool is a subclass of int; True must not canonicalise to 1.0000000000."""
    assert canonical_json({"x": True}) != canonical_json({"x": 1})
    assert '"x":true' in canonical_json({"x": True})


def test_canonical_json_handles_nan_and_infinity() -> None:
    """A fully-clouded AOI legitimately yields NaN; it must still hash."""
    assert '"NaN"' in canonical_json({"x": float("nan")})
    assert '"Infinity"' in canonical_json({"x": float("inf")})
    assert '"-Infinity"' in canonical_json({"x": float("-inf")})


def test_canonical_json_stringifies_unknown_types() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird"

    assert "weird" in canonical_json({"x": Weird()})


# --- reproducibility: bundle and verdict digests ------------------------


def test_identical_bundles_hash_identically() -> None:
    a = bundle(families=all_agreeing(1.0))
    b = bundle(families=all_agreeing(1.0))
    assert bundle_digest(a) == bundle_digest(b)


def test_family_order_does_not_change_the_digest() -> None:
    """The engine's arithmetic is a weighted sum, so order is not information.

    A digest sensitive to ordering would report spurious differences on every
    recompute where a producer returned families in a different sequence.
    """
    fams = all_agreeing(1.0)
    a = bundle(families=fams)
    b = bundle(families=tuple(reversed(fams)))
    assert bundle_digest(a) == bundle_digest(b)


def test_changing_an_agreement_changes_the_digest() -> None:
    a = bundle(families=all_agreeing(1.0))
    b = bundle(
        families=tuple(
            fam(f.family, 0.5 if f.family == "terrain" else f.agreement) for f in all_agreeing(1.0)
        )
    )
    assert bundle_digest(a) != bundle_digest(b)


def test_rewording_a_reason_does_not_change_the_digest() -> None:
    """Prose is explanation, not decision. Editing it must not invalidate a
    stored verdict — otherwise nobody will ever improve an error message."""
    a = bundle(families=(fam("terrain", 1.0, reason="order 3, on channel"),))
    b = bundle(families=(fam("terrain", 1.0, reason="Strahler order 3; on channel"),))
    assert bundle_digest(a) == bundle_digest(b)


def test_changing_the_config_changes_the_bundle_digest() -> None:
    """Weights are an input. A verdict computed under different weights is a
    different verdict and must not silently compare equal."""
    b = bundle(families=all_agreeing(1.0))
    assert bundle_digest(b, EngineConfig()) != bundle_digest(
        b, EngineConfig(agreeing_threshold=0.5)
    )


def test_gates_and_quality_are_part_of_the_digest() -> None:
    base = bundle(families=all_agreeing(1.0))
    other_gate = bundle(families=all_agreeing(1.0), gate=gates(passed=False))
    other_quality = bundle(families=all_agreeing(1.0), metadata_integrity=0.5)
    assert bundle_digest(base) != bundle_digest(other_gate)
    assert bundle_digest(base) != bundle_digest(other_quality)


def test_verdict_digest_is_stable_across_reruns() -> None:
    """The core guarantee: the engine is a pure function, so this must hold."""
    b = bundle(families=all_agreeing(1.0))
    assert verdict_digest(reconcile(b)) == verdict_digest(reconcile(b))


def test_verdict_digest_excludes_prose_but_includes_rule_path() -> None:
    """A verdict reached by a different rule is a different verdict, even at the
    same label and score."""
    from app.services.audit import verdict_payload

    payload = verdict_payload(reconcile(bundle(families=all_agreeing(1.0))))
    assert "rule_path" in payload
    assert "dissent" not in payload


def test_compare_verdicts_reports_identical_for_a_faithful_rerun() -> None:
    b = bundle(families=all_agreeing(1.0))
    result = compare_verdicts(reconcile(b), reconcile(b))
    assert result.identical
    assert result.differences == []
    assert result.as_dict()["identical"] is True


def test_compare_verdicts_names_the_changed_fields() -> None:
    """A digest mismatch alone is an alarm; naming the fields is a diagnosis."""
    stored = reconcile(bundle(families=all_agreeing(1.0)))
    recomputed = reconcile(bundle(families=all_agreeing(-1.0)))
    result = compare_verdicts(stored, recomputed)
    assert not result.identical
    assert result.differences
    joined = " ".join(result.differences)
    assert "label" in joined
    assert "score" in joined
    assert result.stored_digest != result.recomputed_digest


def test_a_weight_change_is_detected_as_a_verdict_difference() -> None:
    """The scenario the guarantee exists for: engine config drifted."""
    b = bundle(families=all_agreeing(0.5))
    stored = reconcile(b, EngineConfig())
    recomputed = reconcile(b, EngineConfig(agreeing_threshold=0.9))
    result = compare_verdicts(stored, recomputed)
    assert not result.identical
    assert any("level" in d or "rule_path" in d for d in result.differences)
