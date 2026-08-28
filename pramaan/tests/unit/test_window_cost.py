"""Tests for the windowed-read cost arithmetic.

`window_cost` is pure and it is load-bearing: its output revised the imagery
budget by 8x and changed the AOI unit from district to sub-watershed
(docs/11 §10). A silent off-by-one in the tile mapping would under-count tiles
and reproduce exactly the error the measurement was written to correct — an
optimistic number that looks plausible.

The synthetic index below mirrors the real granule's geometry measured from
`HLS.S30.T43QGB.2024311T052011.v2.0.B04.tif`: 3660x3660 pixels at 30 m in
256x256 tiles, so 15x15 = 225 tiles.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra in (REPO_ROOT / "backend", REPO_ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from measure_window_cost import TileIndex, window_cost  # noqa: E402

RES = 30.0
# Real bounds of the measured granule (EPSG:32643).
TILE_BOUNDS = (699960.0, 2090220.0, 809760.0, 2200020.0)


def index(byte_per_tile: int = 79511) -> TileIndex:
    """225 tiles, each the mean compressed size of the real granule's tiles."""
    return TileIndex(
        width=3660,
        height=3660,
        tile_w=256,
        tile_h=256,
        byte_counts=tuple([byte_per_tile] * 225),
        band_bytes_on_wire=24_270_000,
        index_bytes_fetched=2048,
        index_requests=8,
    )


def box(x0: float, y0: float, size_m: float) -> tuple[float, float, float, float]:
    """A window `size_m` on a side, offset from the tile's top-left corner."""
    left = TILE_BOUNDS[0] + x0
    top = TILE_BOUNDS[3] - y0
    return (left, top - size_m, left + size_m, top)


# --- geometry ------------------------------------------------------------


def test_grid_shape_matches_the_measured_granule() -> None:
    assert index().tiles_across == 15
    assert index().tiles_across ** 2 == len(index().byte_counts)


def test_a_single_pixel_window_costs_one_tile() -> None:
    """The floor. Anything higher means the mapping over-fetches."""
    tiles, nbytes, w, h = window_cost(index(), box(0, 0, RES), TILE_BOUNDS, RES)
    assert (tiles, w, h) == (1, 1, 1)
    assert nbytes == 79511


def test_a_window_inside_one_tile_still_costs_one_tile() -> None:
    """Tiles are the unit of transfer: a 100 px read inside one tile is one tile."""
    tiles, _, w, h = window_cost(index(), box(0, 0, 100 * RES), TILE_BOUNDS, RES)
    assert tiles == 1
    assert (w, h) == (100, 100)


def test_a_window_straddling_a_tile_boundary_costs_four() -> None:
    """This is why a 2 km site AOI and an 11 km micro-watershed both measured
    1.4 %: both straddle at most a 2x2 block of tiles."""
    # Centre the window on the corner shared by tiles (0,0),(0,1),(1,0),(1,1).
    edge = 256 * RES
    tiles, _, _, _ = window_cost(index(), box(edge - RES, edge - RES, 2 * RES), TILE_BOUNDS, RES)
    assert tiles == 4


def test_the_full_grid_costs_every_tile() -> None:
    """The ceiling, and the district case: 73.7 % of the band on the wire."""
    idx = index()
    tiles, nbytes, w, h = window_cost(idx, TILE_BOUNDS, TILE_BOUNDS, RES)
    assert tiles == 225
    assert (w, h) == (3660, 3660)
    assert nbytes == idx.level0_bytes
    assert nbytes / idx.band_bytes_on_wire == pytest.approx(0.737, abs=0.005)


def test_cost_is_monotonic_in_window_size() -> None:
    """A larger window can never be cheaper. Guards against a sign or
    floor-division error that would silently under-count."""
    idx = index()
    previous = 0
    for size_km in (1, 5, 20, 50, 109):
        _, nbytes, _, _ = window_cost(idx, box(0, 0, size_km * 1000), TILE_BOUNDS, RES)
        assert nbytes >= previous, f"{size_km} km cost less than a smaller window"
        previous = nbytes


# --- clipping and misses -------------------------------------------------


def test_a_window_outside_the_granule_costs_nothing() -> None:
    """A district straddles several MGRS tiles and legitimately misses some.
    That must be free and silent, not an exception — Dharashiv does not
    intersect T43QGB at all."""
    far = (0.0, 0.0, 1000.0, 1000.0)
    assert window_cost(index(), far, TILE_BOUNDS, RES) == (0, 0, 0, 0)


def test_a_window_touching_only_the_exclusive_edge_costs_nothing() -> None:
    """Bounds are half-open at the far edge; a zero-area overlap is a miss."""
    left = TILE_BOUNDS[0]
    degenerate = (left - 1000.0, TILE_BOUNDS[1], left, TILE_BOUNDS[3])
    assert window_cost(index(), degenerate, TILE_BOUNDS, RES)[0] == 0


def test_an_overhanging_window_is_clipped_not_extrapolated() -> None:
    """A window larger than the granule must cost the granule, never more.
    Without clipping the tile ids run past the index and the sum is wrong."""
    idx = index()
    huge = (
        TILE_BOUNDS[0] - 50_000,
        TILE_BOUNDS[1] - 50_000,
        TILE_BOUNDS[2] + 50_000,
        TILE_BOUNDS[3] + 50_000,
    )
    tiles, nbytes, _, _ = window_cost(idx, huge, TILE_BOUNDS, RES)
    assert tiles == 225
    assert nbytes == idx.level0_bytes


def test_a_partly_overhanging_window_costs_only_the_overlap() -> None:
    idx = index()
    overhang = (
        TILE_BOUNDS[0] - 50_000,
        TILE_BOUNDS[3] - 1000,
        TILE_BOUNDS[0] + 1000,
        TILE_BOUNDS[3],
    )
    tiles, _, w, _ = window_cost(idx, overhang, TILE_BOUNDS, RES)
    assert tiles == 1
    assert w == 34, "1000 m at 30 m is 34 px; the 50 km outside must not count"


# --- the claim that drove the decision -----------------------------------


def test_sub_watershed_is_an_order_of_magnitude_cheaper_than_district() -> None:
    """The finding docs/11 §10 rests on, pinned so a refactor cannot erode it.

    The AOI unit is the sub-watershed because `controls.py` requires matched
    controls from the same sub-watershed — so this is the largest window any
    verdict can legitimately read.
    """
    idx = index()
    _, sub_ws, _, _ = window_cost(idx, box(0, 0, 33_000), TILE_BOUNDS, RES)
    _, district, _, _ = window_cost(idx, TILE_BOUNDS, TILE_BOUNDS, RES)
    assert district / sub_ws > 5.0, (
        "if this ratio collapses, whole-district composites stopped being "
        "wasteful and docs/11 §10's conclusion needs re-deriving"
    )


def test_predicted_cost_brackets_the_independently_measured_read() -> None:
    """docs/11 §7 read a real (916, 1004) window and observed 1.8 MiB.

    The prediction depends on tile alignment, so it is a range. The measurement
    must fall inside it — that agreement between two independent methods is the
    only reason the §10 table can be trusted.
    """
    idx = index()
    costs = []
    for off in (0, 500, 1200, 2000):
        # Build the window in pixel terms: 916 x 1004 px at this offset.
        left = TILE_BOUNDS[0] + off * RES
        top = TILE_BOUNDS[3] - off * RES
        _, nbytes, w, h = window_cost(
            idx, (left, top - 1004 * RES, left + 916 * RES, top), TILE_BOUNDS, RES
        )
        assert (w, h) == (916, 1004)
        costs.append(nbytes / 2**20)
    assert min(costs) <= 1.8 <= max(costs), (
        f"measured 1.8 MiB falls outside predicted {min(costs):.2f}-{max(costs):.2f} MiB"
    )
