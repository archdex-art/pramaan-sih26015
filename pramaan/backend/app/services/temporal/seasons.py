"""Indian agricultural seasons, and the type-level ban on cross-season deltas.

docs §17.2 states a hard rule: *"comparisons are always same-season,
year-over-year. Kharif-2022 vs Rabi-2024 is not a comparison; it is a category
error. The engine physically cannot construct a cross-season delta — the API
does not expose it."*

"Physically cannot" is a design requirement, not a wish, so it is enforced here
rather than by convention. `SeasonalObservation` carries its season, and
`seasonal_delta()` refuses two observations whose seasons differ. There is no
code path that produces a cross-season difference, and adding one would require
deleting a raise.

## Why this matters more than it looks

A rabi NDVI of 0.59 and a kharif NDVI of 0.59 mean completely different things in
Marathwada: the first is irrigated winter cropping, the second is monsoon-fed
growth. Differencing across seasons produces a number with no physical
interpretation that nonetheless looks exactly like a result. It is the single
easiest way to manufacture a false "the structure worked" signal.

Season boundaries are an ASSUMPTION (docs §17.2) and configurable per
agro-climatic zone. The defaults below are the document's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Season(StrEnum):
    """Indian cropping seasons as used throughout PRAMAAN."""

    KHARIF = "kharif"  # Jun-Oct: monsoon crop, heavy cloud, water bodies at max
    RABI = "rabi"  # Nov-Feb: THE diagnostic season
    SUMMER = "summer"  # Mar-May: the stress season


#: Month -> season. Kharif Jun-Oct (5), rabi Nov-Feb (4), summer Mar-May (3) = 12.
_MONTH_TO_SEASON: dict[int, Season] = {
    1: Season.RABI,
    2: Season.RABI,
    3: Season.SUMMER,
    4: Season.SUMMER,
    5: Season.SUMMER,
    6: Season.KHARIF,
    7: Season.KHARIF,
    8: Season.KHARIF,
    9: Season.KHARIF,
    10: Season.KHARIF,
    11: Season.RABI,
    12: Season.RABI,
}

#: What each season is diagnostic FOR. Printed in the Evidence Pack so an
#: officer knows why a rabi comparison carries more weight than a kharif one.
SEASON_RATIONALE: dict[Season, str] = {
    Season.KHARIF: (
        "Monsoon crop season. Heavy cloud limits observation and water bodies "
        "are at maximum extent regardless of any intervention, so kharif is the "
        "weakest diagnostic season."
    ),
    Season.RABI: (
        "The diagnostic season. Residual soil moisture and irrigation show up "
        "here — a water-harvesting structure that works makes rabi different."
    ),
    Season.SUMMER: (
        "The stress season. Water persisting into summer is the strongest "
        "available evidence of genuine storage."
    ),
}


class CrossSeasonComparison(ValueError):
    """Raised on any attempt to difference observations from different seasons."""


def season_of(when: date) -> Season:
    return _MONTH_TO_SEASON[when.month]


def season_year(when: date) -> int:
    """The agricultural year a date belongs to.

    Rabi straddles the calendar boundary: Nov-Dec 2023 and Jan-Feb 2024 are the
    SAME rabi season. Labelling them as different years would silently split one
    season into two half-seasons and make a year-over-year comparison compare a
    season against itself.
    """
    if when.month in (11, 12):
        return when.year + 1
    return when.year


@dataclass(frozen=True, slots=True)
class SeasonalObservation:
    """One index value for one season of one year over one AOI."""

    index_name: str
    season: Season
    year: int
    value: float
    #: Fmask-derived usable fraction over the AOI, per the docs/11 finding that
    #: scene metadata is optimistic by ~10 pp.
    data_sufficiency: float
    n_scenes: int
    #: HLS L30+S30 are one harmonised family and may be pooled; Resourcesat is a
    #: separate series and must never be concatenated (the W4 fix).
    sensor_family: str = "HLS"

    def __post_init__(self) -> None:
        if not 0.0 <= self.data_sufficiency <= 1.0:
            raise ValueError(f"data_sufficiency {self.data_sufficiency} outside [0,1]")
        if self.n_scenes < 0:
            raise ValueError(f"n_scenes cannot be negative: {self.n_scenes}")

    @property
    def label(self) -> str:
        return f"{self.season.value} {self.year}"


@dataclass(frozen=True, slots=True)
class SeasonalDelta:
    """A same-season, year-over-year change. Cannot be built any other way."""

    index_name: str
    season: Season
    pre: SeasonalObservation
    post: SeasonalObservation
    delta: float
    #: The weaker of the two windows' sufficiency: a delta is only as trustworthy
    #: as its thinner end.
    data_sufficiency: float

    @property
    def label(self) -> str:
        return f"{self.index_name} {self.season.value} {self.pre.year}->{self.post.year}"


def seasonal_delta(pre: SeasonalObservation, post: SeasonalObservation) -> SeasonalDelta:
    """Difference two observations. Refuses anything but a same-season pair.

    This function is the *only* way to produce a delta, which is what makes the
    "engine physically cannot construct a cross-season delta" claim true rather
    than aspirational.
    """
    if pre.season is not post.season:
        raise CrossSeasonComparison(
            f"refusing to difference {pre.label} against {post.label}: "
            f"cross-season comparison is a category error, not a comparison "
            f"(docs §17.2). A {pre.season.value} value and a {post.season.value} "
            f"value measure different things."
        )
    if pre.index_name != post.index_name:
        raise ValueError(f"refusing to difference {pre.index_name} against {post.index_name}")
    if pre.sensor_family != post.sensor_family:
        raise ValueError(
            f"refusing to difference across sensor families "
            f"({pre.sensor_family} vs {post.sensor_family}): only observations "
            f"within one harmonised product family may be compared (W4 fix). "
            f"Maintain Resourcesat as a separate, parallel series."
        )
    if post.year == pre.year:
        raise ValueError(
            f"pre and post are the same season-year ({pre.label}); a delta needs "
            "two different years"
        )
    if post.year < pre.year:
        raise ValueError(f"post ({post.label}) precedes pre ({pre.label}); windows are ordered")
    return SeasonalDelta(
        index_name=pre.index_name,
        season=pre.season,
        pre=pre,
        post=post,
        delta=post.value - pre.value,
        data_sufficiency=min(pre.data_sufficiency, post.data_sufficiency),
    )
