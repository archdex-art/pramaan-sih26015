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

1. **HLS requires NASA Earthdata authentication** — the primary 30 m source is
   not anonymously downloadable. Free to obtain; must be obtained on day 1.
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
| U1 | Sustained DAAC throughput with credentials, and whether rate-limiting bites at 8 workers | needs an Earthdata account | M1, day 1-2 |
| U2 | Real cloud-free scene availability per season for the demo AOI — the kharif monsoon gap (R-01) | needs the full STAC sweep over 3 years | M1 |
| U3 | Zero-shot per-label accuracy on Indian watershed photographs | needs GT-1 (docs/10: no public corpus exists) | M6 |
| U4 | Matched-control pool size in real sub-watersheds — if N < 5 routinely, the control family is often unavailable and L4 becomes rare | needs loaded watershed polygons | M5 |
| U5 | Whether CartoDEM is obtainable at 30 m for non-government users, or NASADEM must be the fallback | needs Bhoonidhi access attempt | M2 |

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
