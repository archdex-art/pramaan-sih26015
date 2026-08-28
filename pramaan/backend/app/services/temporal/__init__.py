"""Temporal analysis: windows, seasons, trend, matched controls."""

from app.services.temporal.controls import (
    ControlCandidate,
    ControlComparison,
    ControlSet,
    SiteCovariates,
    compare_to_controls,
    select_controls,
)
from app.services.temporal.evidence import (
    TemporalAssessment,
    assess,
    to_control_evidence,
    to_temporal_evidence,
)
from app.services.temporal.seasons import (
    CrossSeasonComparison,
    Season,
    SeasonalDelta,
    SeasonalObservation,
    season_of,
    season_year,
    seasonal_delta,
)
from app.services.temporal.trend import TrendResult, mann_kendall, theil_sen_slope
from app.services.temporal.windows import (
    AnalysisWindows,
    PairingResult,
    build_pairings,
    build_windows,
)

__all__ = [
    "AnalysisWindows",
    "TemporalAssessment",
    "assess",
    "to_control_evidence",
    "to_temporal_evidence",
    "ControlCandidate",
    "ControlComparison",
    "ControlSet",
    "CrossSeasonComparison",
    "PairingResult",
    "Season",
    "SeasonalDelta",
    "SeasonalObservation",
    "SiteCovariates",
    "TrendResult",
    "build_pairings",
    "build_windows",
    "compare_to_controls",
    "mann_kendall",
    "season_of",
    "season_year",
    "seasonal_delta",
    "select_controls",
    "theil_sen_slope",
]
