# 13 — Terrain derivatives and matched controls (measured)

Every number here was measured. Reproduce with:

```
make terrain     # NASADEM -> hydrology -> covariates -> matched controls
make series      # HLS index series at the site and at those controls
make seed        # reconcile and persist
```

## Headline

**Matched controls did not turn the demo into a corroboration.** They turned
"the control family is unavailable" into "the control family was computed, and
the site's change is not distinguishable from comparable un-intervened land".

That is the more valuable outcome. Before M2 the S7 ribbon was drawn dashed and
carried a disclaimer; now it is a real covariate-matched band and the conclusion
is defensible rather than provisional.

## 1. DEM acquisition

| Item | Value |
|---|---|
| Product | NASADEM_HGT.001, 1 arcsec |
| Tiles | `n18e076 n18e077 n18e078 n19e076 n19e077 n19e078` |
| Access | EDL-authenticated, same presign path as HLS (`edl_auth`) |
| Download | 6 tiles, 47.4 MB zipped -> 155 MB `.hgt` |
| Mosaic coverage | **99.9 %** of the hydrology extent |

**One tile was not enough, and the failure was silent.** The site sits 5 km from
the western edge of `n19e077`. A first run on that tile alone produced a DEM
only **37.4 %** valid over the padded extent, so flow accumulation was computed
against nodata upstream and reported a **0.01 km²** catchment — a number that
looks like a site with no drainage rather than like missing data. The script now
refuses to derive hydrology below 99 % coverage.

Effect of the fix on the site's own numbers:

| Variable | One tile (wrong) | Six tiles (correct) |
|---|---|---|
| Slope | 0.85° | 2.03° |
| Flow accumulation | 11 px (0.01 km²) | 160 px (0.14 km²) |
| Distance to stream | *nan* | 276.6 m |

## 2. Hydrology chain

`BreachDepressionsLeastCost(dist=100)` -> `D8Pointer` -> `D8FlowAccumulation`
-> `ExtractStreams` -> `StrahlerStreamOrder` -> `Slope` -> `EuclideanDistance`.

**Breaching, not filling.** Filling a depression raises it to its outlet and
erases the impoundment a check dam exists to create — the structure's own
signature would be removed before the terrain rule ever saw it.

Runtime: **~3.5 s** for a 1492x1568 grid at 30 m, whole chain, on an M5. The
earlier estimate of 6.6 s per district holds comfortably.

### A trap: EuclideanDistance needs an explicit background

`ExtractStreams` writes **nodata** off-stream, and `EuclideanDistance` measures
distance *from* non-background cells. Feeding it the raw streams raster produced
a distance grid that was `0` on streams and nodata everywhere else — no
information at all, in a raster that opened cleanly and reported plausible
metadata. Fixed by writing a 0/1 mask first.

## 3. Stream-initiation threshold

Swept over 12 values; drainage density per threshold:

| Threshold (px) | 25 | 50 | 75 | 100 | 150 | **350** | 500 | 1000 | 2000 |
|---|---|---|---|---|---|---|---|---|---|
| Density (km/km²) | 3.99 | 2.97 | 2.45 | 2.12 | 1.71 | **1.07** | 0.88 | 0.61 | 0.43 |

**Chosen: 350 px** (0.315 km²), density 1.07 km/km², inside the 0.5–2.0 km/km²
range published for semi-arid Deccan basalt.

**[ASSUMPTION — anchored, not fitted.]** The W3 fix asks for calibration against
an authoritative reference drainage network. Bhuvan advertises 24 per-district
`iwmp:*_drn` layers but **WFS is disabled**, so no vector reference was
obtainable (docs/09). This is a literature anchor, and the distinction is
recorded in the persisted `threshold_calibration.basis` rather than smoothed over.

**A sweep must not pick its own endpoint.** The first run swept 200–2000 px,
selected **200** — the lowest value tried — and reported it as "chosen". That is
how a sweep degrades into a default. The script now raises if the optimum lands
on either end of the searched range.

## 4. Covariates over the uncertainty disk

`DiskStat` requires min/median/max, not a pixel: *"a single-pixel sample would
be the most common way to produce a confidently wrong terrain verdict."* The
first version of the script sampled one pixel. The disk shows why that matters:

| Variable | min | median | max | single-pixel |
|---|---|---|---|---|
| Elevation (m) | 383.89 | 384.05 | 386.00 | 384.05 |
| Slope (°) | 0.45 | 2.03 | 2.96 | 2.03 |
| **Flow accumulation (px)** | **1** | **46** | **216** | **160** |
| Strahler order | 0 | 0 | 0 | — |
| Distance to stream (m) | 247.4 | 276.6 | 297.0 | 276.6 |

Flow accumulation varies **200-fold across 15 metres** — the site sits beside a
channel edge. Single-pixel sampling reported 160 px; the disk median is 46. The
rule engine would have had no way to detect that false precision.

`strahler_order` nodata is mapped to **0**, not NaN: `DiskStat` defines 0 as
"not on an extracted channel". Unknown makes the rule abstain; order 0 is what
drives `N3_TERRAIN_PATH` for a check dam. The two lead to opposite verdicts.

## 5. Terrain plausibility

```
TERRAIN RULE — check_dam
  verdict   : implausible        agreement -1.00
  rule_id   : check_dam:implausible
  reason    : Siting is implausible for a check_dam: Strahler order >= 2
              (disk max 0, median 0); distance to stream <= 30 m
              (disk min 247 m, median 277 m). No part of the 15 m location
              uncertainty disk satisfies these constraints.
```

The same site is only **marginal** for a `farm_pond` (−0.40): a farm pond is fed
by field runoff and has no meaningful stream order, so the type-specific
signature reaches a different conclusion from identical terrain.

**[SYNTHETIC CLAIM.]** The site coordinate (77.05, 19.05) is a round number
chosen for the demo, not a surveyed work. So this demonstrates that the rule
fires correctly on real terrain; it is **not** a finding about a real structure.

## 6. Matched controls

| Item | Value |
|---|---|
| Candidates offered | 342 (12-pixel grid over the AOI) |
| **Selected** | **12** (the configured maximum) |
| Insufficient | No |
| Screen | C7 (distance-to-stream similarity), site is off-channel |

Rejections, by reason:

| Reason | Count |
|---|---|
| `dist_to_stream_mismatch` | 276 |
| `beyond_max_controls` | 42 |
| `slope_mismatch` | 11 |
| `too_close_to_intervention` | 1 |

Match quality: selected controls carry slope 1.72–2.24° against the site's 2.03°,
elevation 379–385 m against 384 m, stream distance 277–285 m against 277 m.

## 7. The differenced result

NDVI measured at the site **and at those 12 control positions**, over the same
seasons and the same pairings:

| Season | Site Δ | Control median Δ | Band [p10, p90] | **Differenced** | Percentile | Outside band |
|---|---|---|---|---|---|---|
| rabi 2022→2025 | +0.1157 | +0.0901 | [−0.0217, +0.1363] | **+0.0256** | 75.0 | No |
| summer 2022→2024 | +0.0500 | +0.0307 | [−0.0164, +0.0681] | **+0.0193** | 58.3 | No |

The site did marginally better than its median control in both seasons, and in
neither does it leave the control range. Under docs §17.4 the control family's
agreement is **halved** for a change every control also shows.

## 8. The verdict

```
level      : N1_inconclusive        label: INCONCLUSIVE
score      : -0.0769                confidence: 0.0615
coverage   : 0.80  (4 of 6 families)
rule_path  : N1_DEFAULT -> reason=conflicting_families
             -> agreeing=temporal -> disagreeing=terrain -> score=-0.0769
```

| Family | Agreement | Available | Basis |
|---|---|---|---|
| terrain | **−1.000** | yes | off-channel, order 0, 277 m from drainage |
| satellite | 0.000 | yes | NDVI agrees, MNDWI disagrees — no water signal |
| temporal | +0.822 | yes | same-season NDVI rose as expected |
| control | +0.160 | yes | differenced +0.0256, halved (inside band) |
| context | 0.000 | **no** | CHIRPS not ingested for this AOI |
| photo | — | absent | no model checkpoint (M6) |

**The engine refuses to resolve a genuine conflict.** Terrain says the location
cannot host a check dam; temporal says vegetation rose. It does not average them
into a confident answer — it returns N1 with the conflict named in the rule
path. This is defect C1 (an earlier draft returned `L2 CORROBORATED` at score
≈ 0 from exactly this shape) demonstrably fixed on real data.

## 9. What is still not measured

| Gap | Consequence |
|---|---|
| LULC and soil class | Controls match on slope/elevation/stream distance only; `lulc_class` and `soil_class` are an explicit `unknown` so the matcher treats them as uninformative rather than as agreeing |
| CHIRPS rainfall | `context` family unavailable, coverage capped at 0.80 |
| Reference drainage vector | Threshold anchored to literature, not fitted (§3) |
| Photo model | `photo` family absent (M6) |
| A surveyed claim coordinate | The demo site is synthetic (§5) |

## 10. Operational finding

**CMR STAC rate-limits.** A full 5-year x 2-season sweep reliably draws HTTP
429 part-way through — measured, not anticipated; the first run died at
2021 summer. Exponential backoff with a 30 s cap and 6 attempts recovers, and
the retry count is printed so a throttled run is visibly throttling rather than
mysteriously slow. Same class as the Overpass limit recorded in docs/10.
