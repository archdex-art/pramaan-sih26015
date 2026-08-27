"""Tests for the detectability gate (T11).

The gate's job is to stop the system from treating "we could not see it" as
"it is not there". Every test here is a case where getting that wrong would
produce a false accusation or a wasted compute cycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.terrain import detectability  # noqa: E402
from app.services.terrain.detectability import (  # noqa: E402
    PIXEL_AREA_30M_M2,
    evaluate,
)


def test_check_dam_passes_at_30m() -> None:
    """A check dam impoundment (1,000-10,000 m2) is resolvable at 30 m."""
    r = evaluate("check_dam")
    assert r.passed
    assert r.footprint_pixels == pytest.approx(5500.0 / PIXEL_AREA_30M_M2)
    assert not r.escalated_to_cluster
    assert "enabled" in r.reason


def test_farm_pond_fails_and_escalates_with_enough_neighbours() -> None:
    """The case that pays for the product (docs §16.3 Example B).

    A typical farm pond is 400-2,500 m2. At the 1,450 m2 midpoint that is 1.61
    pixels, which passes; the *specific* 625 m2 work in the demo does not. This
    is exactly why the MIS footprint override exists.
    """
    r = evaluate("farm_pond", expected_footprint_m2=625.0, cluster_member_count=4)
    assert not r.passed
    assert r.footprint_pixels == pytest.approx(625.0 / 900.0)
    assert r.escalated_to_cluster
    assert r.cluster_member_count == 4
    assert "below the sensor detection limit" in r.reason
    assert "cluster of 4 works" in r.reason


def test_gate_failure_without_neighbours_cannot_escalate() -> None:
    r = evaluate("farm_pond", expected_footprint_m2=625.0, cluster_member_count=1)
    assert not r.passed
    assert not r.escalated_to_cluster
    assert "cluster escalation was not possible" in r.reason
    assert "minimum is 3" in r.reason


def test_missing_cluster_count_reports_one_work() -> None:
    r = evaluate("farm_pond", expected_footprint_m2=625.0)
    assert not r.escalated_to_cluster
    assert "1 work(s) within" in r.reason


def test_contour_trench_is_far_below_the_limit() -> None:
    """An individual trench is ~1 m wide — 0.002 pixels."""
    r = evaluate("contour_trench")
    assert not r.passed
    assert r.footprint_pixels < 0.01


def test_type_without_optical_signature_never_passes_the_gate() -> None:
    """CRITICAL: a borewell must not be reported as satellite-assessable.

    Its casing footprint is tiny, so the arithmetic would fail the gate anyway —
    but the *reason* matters. Reporting "too small" implies a finer sensor would
    help. It would not: there is no optical signature at any resolution.
    """
    r = evaluate("borewell")
    assert not r.passed
    assert not r.optically_assessable
    assert not r.escalated_to_cluster
    assert "no reliable optical signature at any resolution" in r.reason


def test_non_assessable_type_with_huge_footprint_still_blocked() -> None:
    """Proves the type rule dominates the arithmetic, not the other way round."""
    r = evaluate("dug_well", expected_footprint_m2=100_000.0)
    assert not r.passed
    assert not r.optically_assessable
    assert r.footprint_pixels > 100


def test_min_pixels_default_is_above_one_pixel() -> None:
    """A structure of exactly one pixel area is not reliably detectable.

    It is not pixel-aligned: a 900 m2 square typically spreads across four
    pixels at ~25% each. The default threshold must therefore exceed 1.0.
    """
    assert detectability.DEFAULT_MIN_PIXELS > 1.0
    exactly_one_pixel = evaluate("percolation_tank", expected_footprint_m2=900.0)
    assert not exactly_one_pixel.passed
    assert exactly_one_pixel.footprint_pixels == pytest.approx(1.0)


def test_footprint_override_beats_type_default() -> None:
    """A 3 ha tank and a 0.2 ha tank are both `percolation_tank`."""
    small = evaluate("percolation_tank", expected_footprint_m2=1_000.0)
    large = evaluate("percolation_tank", expected_footprint_m2=30_000.0)
    assert not small.passed
    assert large.passed


def test_finer_pixel_area_can_flip_the_gate() -> None:
    """The gate is a function of the sensor, and says so.

    A 625 m2 farm pond is undetectable at 30 m and detectable at 10 m. This is
    the P2 upgrade path made explicit rather than asserted.
    """
    at_30m = evaluate("farm_pond", expected_footprint_m2=625.0, pixel_area_m2=900.0)
    at_10m = evaluate("farm_pond", expected_footprint_m2=625.0, pixel_area_m2=100.0)
    assert not at_30m.passed
    assert at_10m.passed


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"pixel_area_m2": 0.0}, "pixel_area_m2 must be positive"),
        ({"pixel_area_m2": -900.0}, "pixel_area_m2 must be positive"),
        ({"min_pixels": 0.0}, "min_pixels must be positive"),
        ({"expected_footprint_m2": -1.0}, "cannot be negative"),
    ],
)
def test_invalid_inputs_fail_loudly(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        evaluate("check_dam", **kwargs)  # type: ignore[arg-type]


def test_unknown_type_fails_loudly() -> None:
    with pytest.raises(KeyError, match="no expected signature"):
        evaluate("moon_base")


def test_lineage_is_complete_for_reproducibility() -> None:
    r = evaluate("farm_pond", expected_footprint_m2=625.0, cluster_member_count=4)
    lineage = r.lineage()
    for key in (
        "expected_footprint_m2",
        "pixel_area_m2",
        "footprint_pixels",
        "min_pixels_required",
        "escalated_to_cluster",
        "cluster_member_count",
        "optically_assessable",
    ):
        assert key in lineage, key
