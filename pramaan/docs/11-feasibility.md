# 11 — Feasibility analysis (measured)

Every number in this document was measured, not estimated. Where a figure is an
extrapolation from a measurement, the measurement and the arithmetic are both
shown. Where something could not be verified, it is listed as an open unknown
rather than assumed favourable.

Host used for compute benchmarks: Apple M5, 10 threads, macOS 25.5. The demo VM
in the plan is 8 vCPU / 32 GB / no GPU, x86. x86 is assumed **5× slower** than
this host throughout, which is deliberately pessimistic.

---

## Verdict

**Feasible.** No measured result threatens the plan. Two assumptions in the
design document were wrong in ways that matter, and both have fixes that cost
architecture rather than schedule:

1. **HLS requires NASA Earthdata authentication, and the obvious way to use the
   token does not work.** Resolved with a credential in hand — see §7. GDAL
   fails with a misleading "not recognized as being in a supported file format"
   because LP DAAC redirects to a CloudFront presigned URL and GDAL forwards the
   bearer header onto it, which AWS rejects. Fixed in
   `app/services/satellite/edl_auth.py`.
2. **Bulk granule download is infeasible; windowed COG reads are essential.**
   Naive downloading is ~78 GB for the demo corpus. Windowed reads bring the
   same corpus to ~7 GB. This is an architectural constraint on the satellite
   worker, not a scheduling risk.

Three claims that were unverified are now verified comfortably: no-GPU
inference, hydrology compute, and engine throughput at national scale.

---

## 1. Photo inference without a GPU (constraint C-3)

`google/siglip-base-patch16-224`, 203 M params, CPU only, 8 prompts covering the
engine label set, `torch 2.13`, 10 threads.

| Batch | ms/batch | ms/image | img/s |
|---|---|---|---|
| 1 | 132 | 132 | 7.6 |
| 4 | 198 | 49 | 20.3 |
| 8 | 301 | 38 | 26.6 |
| **16** | **538** | **34** | **29.7** |

**Extrapolated to the demo VM at 5× slower: ~5.9 img/s.**

| Workload | This host | Demo VM (5× pessimistic) |
|---|---|---|
| 1,200-image GT-1 corpus | 40 s | 3.4 min |
| 300-image district monthly batch | 10 s | 51 s |
| Single claim, interactive | 34 ms | 170 ms |

**Conclusion: C-3 is satisfied with very large margin.** No GPU is required at
any point in the demo path, and a single-claim inference is well inside an
interactive budget. The `--no-gpu` posture is not a compromise; it is simply
adequate.

Caveat: this measures SigLIP-base. SigLIP-2 variants are larger and would be
slower by roughly their parameter ratio. Even a 3× larger model leaves ~2 img/s
on the demo VM, which still clears every workload above.

---

## 2. Imagery volume and throughput (risk R-40)

### Measured facts

| Fact | Value | How measured |
|---|---|---|
| HLS S30 band, full tile, compressed | **26.45 MB** | HEAD on an LP DAAC asset |
| HLS bands published per scene | 14 (B01-B12, B8A, Fmask) | CMR STAC item |
| **HLS download requires auth** | **HTTP 401** | ranged GET returned `HTTP Basic: Access denied` |
| Sentinel-2 L2A 10 m band, full tile | **217-237 MiB** | HEAD on AWS open-data COG |
| Sentinel-2 20 m band (SWIR) | 58 MiB | same |
| Sentinel-2 SCL mask | 4.5 MiB | same |
| Full S2 tile, uncompressed | 230 MiB (10,980²  uint16) | opened via GDAL `/vsicurl` |
| AOI window as fraction of tile | **8.9 %** | 0.30° box against a 110 km tile |
| Windowed read, one band | **1.8 MiB in 2.46 s** | `rasterio` window read over HTTPS |
| COG overviews present | `[2, 4, 8, 16]` | yes — enables cheap previews |

### The decisive finding

A naive "download the granule" pipeline and a windowed-read pipeline differ by
**an order of magnitude** on the same corpus:

```
Demo corpus assumed: 2 districts x 4 HLS tiles x 3 years x 3 seasons
                     x 6 usable scenes x 7 needed bands = 3,024 band-reads

Naive granule download   3,024 x 26.45 MB           = 78 GB
Windowed COG read        3,024 x 26.45 MB x 0.089   =  7.1 GB
```

At a realistic sustained 5 MB/s the naive path is **4.3 hours of pure transfer
at best**, and in practice much worse against a throttled government or DAAC
endpoint. The windowed path is ~7 GB.

Windowed reads are **latency-bound, not bandwidth-bound**: the measured 2.46 s
for 1.8 MiB is dominated by HTTP range-request round trips, not throughput. So
the fix is concurrency, not a bigger pipe:

```
3,024 band-reads x ~2.5 s = 2.1 hours serial
                          = ~16 min at 8 concurrent workers
```

### Consequences for the build

- The satellite worker **must** use `odc-stac`/`rioxarray` windowed reads against
  COGs. A `requests.get(href)` implementation would silently turn a 16-minute
  job into an overnight one. This belongs in the code review checklist for M5.
- Set `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` and
  `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif`. Without the first, GDAL issues a
  directory listing per open and the read cost roughly doubles.
- Concurrency target 8 workers. Higher risks rate-limiting; the Overpass survey
  in docs/10 got connection-reset after sustained querying, and DAAC endpoints
  behave similarly.
- Prefer **HLS over raw Sentinel-2**: at 30 m an HLS band is 26 MB against
  217 MB for a 10 m S2 band, and 30 m is the tier the problem statement names.
  The auth cost is a one-off registration.

**Risk R-40 is downgraded from "could kill the project" to "architectural
constraint, mitigated."** Its residual form is: *if the team implements bulk
download instead of windowed reads, the schedule fails.* That is a code-review
item, not an unknown.

---

## 3. DEM hydrology compute

WhiteboxTools v2.4.0, full chain on a synthetic DEM sized to a real Marathwada
district: **6,100 × 5,000 px = 30.5 Mpx**, i.e. ~150 × 185 km at 30 m, 93 MB on
disk compressed.

| Step | Time |
|---|---|
| `BreachDepressionsLeastCost` (dist=100) | 3.7 s |
| `D8Pointer` | 0.3 s |
| `D8FlowAccumulation` | 0.7 s |
| `Slope` | 0.8 s |
| `ExtractStreams` | 0.3 s |
| `StrahlerStreamOrder` | 0.8 s |
| **Full chain, one district** | **6.6 s** |
| Two demo districts | 13 s |

**Conclusion: the hydrology has no compute risk whatsoever.** Task T05 is
budgeted at 16 hours; essentially none of that is computation. It is DEM
acquisition, CRS handling, mosaicking, and the threshold calibration sweep. Plan
T05 as a data-wrangling task and do not let the 16 hours be spent optimising
something that runs in seven seconds.

The `whitebox` Python package self-installs its binary on first use (verified:
downloads and unpacks `WhiteboxTools_darwin_m_series.zip`), so containerisation
needs the binary baked into the image or a warm cache volume — otherwise first
run in a fresh container fetches it, which breaks the offline demo guarantee.

---

## 4. Engine and producer throughput

Reproducible via `python scripts/benchmark.py`. 5,000 synthetic six-family
bundles, randomised agreements, availabilities, quality and gates.

| Measure | Value |
|---|---|
| Per verdict | **12.6 µs** |
| Throughput | **79,249 verdicts/s** |
| One district (1,200 works) | 15.1 ms |
| One state (40 districts, 48,000 works) | 0.61 s |
| **National (1.24 lakh WDC-PMKSY structures)** | **1.56 s** |
| Terrain plausibility | 2.3 µs/claim |
| Detectability gate | 1.2 µs/claim |
| Both producers, one district | 4.1 ms |

**The decision layer is free.** Every performance concern in this system is in
raster IO, never in reconciliation. The §26 target of p95 < 3 s per
reconciliation is dominated entirely by fetching evidence, which is what the
per-sub-watershed indicator cube (the W7 fix) exists to eliminate.

### Two invariants confirmed at scale

Over 5,000 randomised bundles:

- **Every** dissent panel non-empty.
- **Invariant I1** (`confidence ≤ |score|`) holds on every verdict.
- **`N3_contradicted` occurred 0 times.** Contradictions are unreachable on
  random evidence because both named paths require an actively excluded
  alternative. You cannot stumble into accusing anybody — which is the whole
  design intent of the two-path structure, now demonstrated rather than argued.

### Level distribution on random evidence

| Level | Share |
|---|---|
| N1 inconclusive | 56.3 % |
| N2 unsupported | 20.9 % |
| L2 corroborated | 7.7 % |
| L3 multi-indicator | 6.4 % |
| L1 observed | 6.2 % |
| L4 control-differenced | 2.6 % |
| **N3 contradicted** | **0.0 %** |

Under noise the engine says "I do not know" 56 % of the time. That is the
designed conservatism, measured.

---

## 5. Access and credentials

| Dependency | Status | Action |
|---|---|---|
| NASA Earthdata (HLS) | **401 without auth** | Free registration — **day 1 blocker** |
| Copernicus Data Space | open, collection id `sentinel-2-l2a` | none |
| AWS Sentinel-2 COGs | open, no auth | none |
| Bhuvan WMS (LULC, drainage, waterbodies) | open, ~95 s / 6.7 MB capabilities | cache at onboarding |
| Bhuvan WFS | **not enabled** (0 feature types) | rasterise via GetMap |
| NRSC Bhoonidhi | government credential we do not hold | documented substitute (C-2) |
| Mapillary | free token; **rejected for GT-1 on measured evidence** | see docs/10 |
| Places365 | form-gated **and** non-commercial | excluded (C-7) |
| JRC GSW / LUCAS / AgriFieldNet | open | none |

---

## 6. Open unknowns

Listed because they are not yet measured, not because they are expected to fail.

| # | Unknown | Why it is not yet measurable | When it resolves |
|---|---|---|---|
| ~~U1~~ | ~~Sustained DAAC throughput and rate-limiting at 8 workers~~ | **RESOLVED — see §7** | measured 2026-08-28 |
| ~~U2~~ | ~~Cloud-free scene availability per season (R-01)~~ | **RESOLVED — see §8** | measured 2026-08-28 |
| U3 | Zero-shot per-label accuracy on Indian watershed photographs | needs GT-1 (docs/10: no public corpus exists) | M6 |
| U4 | Matched-control pool size in real sub-watersheds — if N < 5 routinely, the control family is often unavailable and L4 becomes rare | needs loaded watershed polygons | M5 |
| U5 | Whether CartoDEM is obtainable at 30 m for non-government users, or NASADEM must be the fallback | needs Bhoonidhi access attempt | M2 |
| U6 | Whether the EDL presigned URL's validity window outlives a long district ingest, or resolution must be re-run mid-job | needs a full-district run | M5 |

**U4 is the one worth watching.** It does not threaten delivery, but if matched
controls are usually unavailable then L4 verdicts are rare and the "paired
control differencing" headline weakens. Measure it as soon as watershed polygons
load, and be ready to report the control-availability rate honestly rather than
quietly widening the matching criteria until controls appear.

---

## Reproducing

```bash
python scripts/benchmark.py           # engine + producers
python scripts/verify_endpoints.py    # external endpoint liveness
python scripts/verify_datasets.py     # dataset sources
```

Sections 1-3 require multi-GB downloads and a model checkpoint, so they are
documented with their methods rather than run in CI. The commands are in this
file's git history.

---

## 7. Authenticated HLS access — measured with a real token

Token: `uid=danimma`, assurance level 3, issued 2026-08-28, **expires
2026-10-27** (60-day window). Supplied out-of-band, used via `EARTHDATA_TOKEN`
only, absent from the tree.

### The access path, and why the obvious one fails

```
GET  https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/...B04.tif
     Authorization: Bearer <token>
->   303 See Other
     Location: https://d1nklfio7vscoe.cloudfront.net/...?X-Amz-Signature=...
->   fetched WITHOUT Authorization: 206, magic bytes `II` = valid TIFF
```

The presigned URL needs no credentials. GDAL sets custom headers via
`CURLOPT_HTTPHEADER`, which curl replays on every hop of a redirect chain, so it
forwards the bearer token to CloudFront — and AWS rejects a request carrying
both a presigned query signature and an `Authorization` header. GDAL surfaces
that as:

```
'/vsicurl/https://...B04.tif' not recognized as being in a supported file format.
```

which reads like a corrupt file and is actually an auth failure. **Four
mechanisms were tried and all four fail identically:** `GDAL_HTTP_HEADERS`,
`GDAL_HTTP_BEARER`, `GDAL_HTTP_AUTH=BEARER`, and a cookie jar.

**Fix:** resolve the 303 ourselves, hand GDAL the presigned URL with no auth.
Implemented and unit-tested in `app/services/satellite/edl_auth.py`. The
load-bearing test asserts exactly **one** request reaches the DAAC host, because
"simplifying" this to `follow_redirects=True` reintroduces the bug silently.

### Direct S3 is not available off-region

`/s3credentials` returns HTTP 200 with temporary credentials, but the granted
role is `s3-same-region-access-role` and every call from outside `us-west-2` is
denied:

```
User: .../s3-same-region-access-role/danimma is not authorized to perform:
s3:ListBucket ... with an explicit deny in an identity-based policy
```

So HTTPS + presign is the only path for a demo VM anywhere but AWS us-west-2 —
including the SIH venue.

### Concurrency has a measured optimum, and more is worse

16 authenticated windowed band-reads over the demo AOI, zero errors at every
level:

| Workers | Wall (s) | reads/s | vs serial |
|---|---|---|---|
| 1 | 74.2 | 0.22 | 1.0× |
| 4 | 23.5 | 0.68 | 3.1× |
| **8** | **14.0** | **1.14** | **5.2×** |
| 12 | 26.4 | 0.61 | 2.8× — **throttled** |

**12 workers is slower than 4.** Server-side throttling engages between 8 and
12, so `OPTIMAL_CONCURRENCY = 8` is pinned in code with this table in the
docstring and a test asserting the value, so raising it requires a new
measurement rather than an intuition.

### Corpus budget, now from measurement rather than estimate

| Quantity | Measured |
|---|---|
| Windowed band-read over the demo AOI | **2.26 MiB** |
| Time per read (serial, incl. presign round-trip) | ~4.5-6.5 s |
| Best sustained rate | 1.14 reads/s at 8 workers |
| 3,024 band-reads (2 districts x 3 yr x 3 seasons) | **6.7 GB, ~44 min** |

44 minutes, one-time, offline-cacheable, against 4.3+ hours for the naive
granule path. The earlier 16-minute estimate assumed 2.5 s/read; real reads are
~5 s once the presign round-trip is counted. **44 min is the number to plan
with.**

### End-to-end proof

STAC search -> EDL presign -> windowed COG read -> Fmask bit unpack -> NDVI,
on `HLS.S30.T43QGB.2024311T052011.v2.0` (3 % cloud, rabi 2024) over the demo AOI:

```
B04    (1119, 1059)  2.26 MiB in 6.3 s
B08    (1119, 1059)  2.26 MiB in 4.7 s
Fmask  (1119, 1059)  1.13 MiB in 4.4 s

usable pixels : 1,184,481 / 1,185,021  (100.0 %)
NDVI mean     : +0.5843
NDVI p10/p90  : +0.4017 / +0.7374
```

A rabi-season NDVI of 0.58 over cropland in Marathwada is physically plausible.
**The entire satellite evidence path works against real data.** This was the
single largest unverified assumption in the project.

---

## 8. Seasonal cloud availability — risk R-01 quantified

The plan rates persistent monsoon cloud as its highest-impact data risk
(P=5, I=3) and mitigates it with "rabi/summer carry the analysis". That
mitigation is now measured and correct.

HLS S30 + L30, demo AOI, scene-level `eo:cloud_cover`, 3 years:

| Season | Scenes | < 20 % cloud | < 40 % | Median cloud |
|---|---|---|---|---|
| **kharif** (Jun-Sep, monsoon) | 255 | **27 (10.6 %)** | 46 | **66-82 %** |
| **rabi** (Nov-Feb) | 446 | **335 (75.1 %)** | 369 | **1-6 %** |
| **summer** (Mar-May) | 367 | **265 (72.2 %)** | 297 | **1-3.5 %** |

Per-year kharif detail shows how variable it is: **2022 gave 3 clear scenes,
2023 gave 19, 2024 gave 5.**

### What this means

- **Rabi and summer are data-rich**, ~110-125 usable scenes per season per year.
  Seasonal compositing has ample input, and same-season year-over-year
  comparison is comfortably supported.
- **Kharif is thin but not empty**: ~9 clear scenes per year on average, and as
  few as 3 in a bad year. A monsoon-window composite is possible in a good year
  and legitimately impossible in a bad one.
- **The engine's data-sufficiency floor will fire on kharif and pass on
  rabi/summer, and that is correct behaviour, not a defect.** Golden case 11
  already encodes exactly this outcome (`data_sufficiency 0.18` -> N1
  INCONCLUSIVE). The measured cloud statistics are the empirical justification
  for that case existing.

### Caveat, stated because it cuts the other way

These are **scene-level** cloud percentages. The design insists on
*AOI-specific* usable fraction (a scene 79 % cloudy may be clear over a small
sub-watershed), so these figures are conservative for a small AOI. The
per-AOI figure will be better than the table above — but it must be computed,
not assumed, and it is what `data_sufficiency` should carry.
