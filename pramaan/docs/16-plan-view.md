# 16 — Plan view (S3)

The thematic map product PS 26015 asks for, and the reason it did not exist
until now.

## What the problem statement asked for

PS 26015's expected solutions include, verbatim, *"land use maps, drainage maps,
vegetation maps, watershed intervention maps, and spatial change detection
products."* Three of those five now exist:

| Asked for | Where it is | State |
|---|---|---|
| Drainage map | S3 plan view — D8 network, Strahler-weighted | Measured |
| Watershed intervention map | S3 — intervention pin, uncertainty disk, footprint, AOI | Measured |
| Spatial change detection | S7 temporal chart — site vs 12 matched controls | Measured |
| Land use map | Not built. LULC stays `unknown` in the matcher | **Absent** |
| Vegetation map | Not built as a map. NDVI exists as a *series*, not a raster surface | **Absent** |

The last two rows are gaps, not features. They are listed here so the gap is on
the record rather than discovered by a reviewer.

## Why there was no map before

The standing objection was that *a convincing-looking empty map is the most
dishonest thing this product could ship*. A map is the single easiest surface on
which to imply precision that the data does not support: a pin at a coordinate
looks authoritative regardless of whether anything was measured there.

That objection stopped applying when the geometry became real. Every layer is
now one the analysis itself consumed:

- **Drainage** — the D8 network traced from six mosaicked NASADEM tiles by the
  same WhiteboxTools chain, at the same calibrated stream-initiation threshold
  (350 px, 1.07 km/km²), that produced the terrain verdict.
- **Intervention** — the claim's own recorded coordinate.
- **Controls** — the twelve sites `controls.select_controls` selected from 342
  candidates on DEM-derived slope, elevation and distance-to-stream.

## Method

### Drainage is traced by flow direction, not by contouring

Every stream cell emits one segment to its **D8 downstream neighbour**. This
keeps the network's topology and flow direction, and carries Strahler order per
segment. Contour-tracing the stream raster instead would produce smoother curves
and discard the topology — and topology is the part that matters, because
Strahler order is precisely what the terrain rule tests.

Line weight is therefore Strahler order. A network rendered at uniform weight
would be drawing a different quantity from the one being argued.

### There is no basemap, deliberately

Two reasons, and the first is binding:

1. §38 requires the console to work with the network interface physically
   disabled. A tile request is exactly the dependency that breaks on a venue
   network.
2. A slippy basemap makes this read as a consumer map product. A survey plan
   reads as a record, which is what it is.

### Projection

Equirectangular with a `cos(lat)` correction on longitude, anchored at the
window centre. Over a 10 km window at 19°N this is accurate to well under a
pixel, and unlike Web Mercator it states its own assumption.

## The one thing on this screen that is not to scale

The uncertainty disk and the footprint square are drawn at the plan's own scale
of **16.0 m per SVG unit**. At that scale a 3,200 m² check dam is under three
units across — physically correct and visually absent.

So the site carries a **fixed-size locator ring**, and the legend says in words
that the ring is not to scale. Silently inflating the disk to make the site
visible would be the map lying about precision, which is the exact failure this
screen was withheld to avoid. The disk *is* drawn to scale against the 30 m
pixel grid on S2, which is where the detectability gate is argued.

## What the map made visible

The demo claim's terrain verdict — `implausible`, agreement −1.00 — is a number
on S2. On the plan it is a picture: the check dam sits **277 m from the nearest
extracted channel**, out on the interfluve, where the expected signature for a
check dam requires Strahler order ≥ 2 and a channel within 30 m.

The twelve controls are **also** off-channel — 256 m to 296 m to the nearest
stream, matched to the site's 277 m. That is what matching means, and the plan
makes it checkable: the controls are not "good" sites the claim is measured
against, they are sites with the *same* terrain as the claim and no
intervention. The verdict stops being an assertion and becomes something a
reviewer can verify by eye.

## Reproduce

```bash
make terrain   # NASADEM -> hydrology -> covariates -> matched controls
make map       # vectorise streams/strahler/d8_pointer -> data/demo/map_layers.json
```

`make map` reads the same rasters the terrain verdict was computed from, so the
plan cannot drift from the analysis. Output is 359 KiB and **is committed**,
following the same rule as `terrain.json` and `temporal_series.json`: the DEM
tiles are 223 MB and gitignored, so a fresh clone must still be able to render
the plan.

## Verification

- 6 integration tests (`tests/integration/test_verdict_api.py`, `-k map`),
  97 % coverage of `app/api/v1/mapview.py`; the 2 uncovered lines are the
  "layers not built" branches, unreachable when the layers exist.
- Tests assert what would silently degrade the map into decoration: Strahler
  order surviving transport, no zero-length segments, every point inside the
  declared window, controls carrying the covariates they were matched on, and
  stated provenance for all four layers.
- Contrast measured on every text pair on the screen: min 4.59:1, zero AA
  failures. Zero horizontal overflow at 375 / 768 / 1024 / 1440 px.

## Known gaps

- **No LULC or vegetation surface.** Both are listed as absent above.
- **The micro-watershed polygon is not drawn.** The AOI rectangle is a bounding
  box, not a delineated catchment boundary, and is labelled "Analysis AOI"
  rather than "watershed" for that reason.
- **One AOI.** `map_layers.json` is a snapshot of the demo district. A second
  district requires a second `make terrain` / `make map` pass.
