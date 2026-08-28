"""PRE/POST window construction (docs §17.1).

```
                 construction period
                    +------------+
---------PRE--------|            |----------POST--------------
   T-24mo ...  T-3mo |  T claimed |  T+3mo  ...  T+24mo
                    +------------+
                     EXCLUDED from both windows
```

Two decisions, both of which a remote-sensing reviewer will look for:

**Why exclude +/-3 months.** During construction the surface is disturbed — bare
earth, spoil heaps, machinery, cleared vegetation. Including it manufactures a
fake "degradation then recovery" signal that looks exactly like a successful
intervention. Excluding it is cheap and it is the difference between measuring an
outcome and measuring an earthworks.

**Why 24 months each side.** Two observations of every season is the minimum for
any same-season comparison. A project younger than that cannot support a
year-over-year delta in every season, so the engine says so and the epistemic
level is capped rather than the window quietly shrunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.temporal.seasons import (
    Season,
    SeasonalObservation,
)

#: Months excluded either side of the claimed completion date.
CONSTRUCTION_BUFFER_MONTHS = 3

#: Months of analysis window either side of the buffer.
WINDOW_MONTHS = 24

#: Same-season year-over-year needs at least this many years per side.
MIN_YEARS_PER_SIDE = 2

#: A season-year is only usable as a window endpoint above this Fmask-derived
#: sufficiency. Mirrors the engine's own floor so a delta the engine would refuse
#: to act on is not built in the first place.
MIN_OBSERVATION_SUFFICIENCY = 0.35


def _shift_months(when: date, months: int) -> date:
    """Add/subtract whole months, clamping the day to the target month's length."""
    total = when.month - 1 + months
    year = when.year + total // 12
    month = total % 12 + 1
    day = when.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # 31 Jan -> 28/29 Feb etc.


@dataclass(frozen=True, slots=True)
class AnalysisWindows:
    """The PRE/POST windows for one claim, plus the excluded construction band."""

    claimed_date: date
    pre_start: date
    pre_end: date
    post_start: date
    post_end: date

    @property
    def excluded_start(self) -> date:
        return self.pre_end

    @property
    def excluded_end(self) -> date:
        return self.post_start

    def contains_pre(self, when: date) -> bool:
        return self.pre_start <= when < self.pre_end

    def contains_post(self, when: date) -> bool:
        return self.post_start < when <= self.post_end

    def is_excluded(self, when: date) -> bool:
        """True inside the construction band, which belongs to neither window."""
        return self.pre_end <= when <= self.post_start

    def describe(self) -> str:
        return (
            f"PRE {self.pre_start.isoformat()}..{self.pre_end.isoformat()}, "
            f"construction band {self.pre_end.isoformat()}.."
            f"{self.post_start.isoformat()} EXCLUDED, "
            f"POST {self.post_start.isoformat()}..{self.post_end.isoformat()}"
        )

    def lineage(self) -> dict[str, object]:
        return {
            "claimed_date": self.claimed_date.isoformat(),
            "pre_start": self.pre_start.isoformat(),
            "pre_end": self.pre_end.isoformat(),
            "post_start": self.post_start.isoformat(),
            "post_end": self.post_end.isoformat(),
            "construction_buffer_months": CONSTRUCTION_BUFFER_MONTHS,
            "window_months": WINDOW_MONTHS,
        }


def build_windows(
    claimed_date: date,
    *,
    buffer_months: int = CONSTRUCTION_BUFFER_MONTHS,
    window_months: int = WINDOW_MONTHS,
) -> AnalysisWindows:
    """Construct the PRE/POST windows around a claimed completion date."""
    if buffer_months < 0:
        raise ValueError(f"buffer_months cannot be negative: {buffer_months}")
    if window_months <= 0:
        raise ValueError(f"window_months must be positive: {window_months}")
    pre_end = _shift_months(claimed_date, -buffer_months)
    post_start = _shift_months(claimed_date, buffer_months)
    return AnalysisWindows(
        claimed_date=claimed_date,
        pre_start=_shift_months(pre_end, -window_months),
        pre_end=pre_end,
        post_start=post_start,
        post_end=_shift_months(post_start, window_months),
    )


@dataclass(frozen=True, slots=True)
class SeasonPairing:
    """One season's PRE and POST observations, ready to be differenced."""

    season: Season
    pre: SeasonalObservation
    post: SeasonalObservation


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Everything build_pairings found, including why some seasons were dropped."""

    pairings: tuple[SeasonPairing, ...]
    #: Season -> human-readable reason it could not be paired.
    skipped: dict[Season, str]
    #: True when the observation record is too short for a same-season
    #: comparison in ANY season. The engine caps the level when this is set.
    insufficient_history: bool

    def seasons_available(self) -> tuple[Season, ...]:
        return tuple(p.season for p in self.pairings)


def observation_window(obs: SeasonalObservation, windows: AnalysisWindows) -> str | None:
    """Classify an observation as 'pre', 'post', or None (excluded/outside).

    Uses the observation's season-year midpoint rather than a scene date,
    because an observation is a seasonal composite: it has no single date, and
    picking one arbitrarily would put a rabi composite on the wrong side of a
    construction band that falls in December.
    """
    mid = _season_midpoint(obs.season, obs.year)
    if windows.is_excluded(mid):
        return None
    if windows.contains_pre(mid):
        return "pre"
    if windows.contains_post(mid):
        return "post"
    return None


def _season_midpoint(season: Season, year: int) -> date:
    """Representative date for a season-year.

    Rabi spans Nov(y-1)..Feb(y), so its midpoint is around 1 January of `year` —
    which is exactly why `season_year` assigns Nov/Dec to the following year.
    """
    if season is Season.KHARIF:
        return date(year, 8, 15)  # mid Jun-Oct
    if season is Season.RABI:
        return date(year, 1, 1)  # mid Nov(y-1)-Feb(y)
    return date(year, 4, 15)  # mid Mar-May


def build_pairings(
    observations: list[SeasonalObservation],
    windows: AnalysisWindows,
    *,
    min_sufficiency: float = MIN_OBSERVATION_SUFFICIENCY,
) -> PairingResult:
    """Pair PRE and POST observations within each season.

    Never across seasons — the pairing is built per season and handed to
    `seasonal_delta`, which would refuse a mismatch anyway. Belt and braces on
    the rule docs §17.2 calls a category error.

    When a season has several candidates per side, the one with the highest data
    sufficiency wins. Choosing the *closest in time* would be the obvious
    alternative and is worse: it would prefer a cloud-wrecked composite adjacent
    to the construction band over a clean one a year out.
    """
    per_season: dict[Season, dict[str, list[SeasonalObservation]]] = {}
    for obs in observations:
        if obs.data_sufficiency < min_sufficiency:
            continue
        side = observation_window(obs, windows)
        if side is None:
            continue
        per_season.setdefault(obs.season, {"pre": [], "post": []})[side].append(obs)

    pairings: list[SeasonPairing] = []
    skipped: dict[Season, str] = {}
    for season in Season:
        sides = per_season.get(season)
        if not sides:
            skipped[season] = "no observations met the data-sufficiency floor in either window"
            continue
        pre_pool, post_pool = sides["pre"], sides["post"]
        if not pre_pool:
            skipped[season] = (
                f"{len(post_pool)} usable POST observation(s) but none in PRE — "
                "no baseline to compare against"
            )
            continue
        if not post_pool:
            skipped[season] = (
                f"{len(pre_pool)} usable PRE observation(s) but none in POST — "
                "nothing to compare the baseline to"
            )
            continue
        best_pre = max(pre_pool, key=lambda o: (o.data_sufficiency, o.n_scenes))
        best_post = max(post_pool, key=lambda o: (o.data_sufficiency, o.n_scenes))
        pairings.append(SeasonPairing(season=season, pre=best_pre, post=best_post))

    return PairingResult(
        pairings=tuple(pairings),
        skipped=skipped,
        insufficient_history=not pairings,
    )
