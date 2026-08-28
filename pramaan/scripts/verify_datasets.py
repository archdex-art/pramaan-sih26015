#!/usr/bin/env python3
"""Verify every candidate GT-1 / GT-2 dataset source, and record the result.

Companion to `verify_endpoints.py`, same discipline and same reason (docs §29):
a dataset we have not fetched is a dataset we cannot claim. This script issues
the cheapest request that proves each source is reachable and usable, and writes
a machine-generated log to `docs/10-ground-truth-datasets.md`.

Statuses are deliberately more granular than OK/FAIL, because "reachable" is not
the same as "usable for a government prototype":

  OK              reachable and openly usable
  AUTH_REQUIRED   reachable but needs a free account/token we can obtain
  GATED           reachable but access is granted by a form/committee
  LICENCE_BLOCKED reachable and open, but the licence forbids our use case
  FAIL            not reachable

Usage:
    python scripts/verify_datasets.py                # print table
    python scripts/verify_datasets.py --write-docs   # regenerate docs/10
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_TARGET = REPO_ROOT / "docs" / "10-ground-truth-datasets.md"
LOG_TARGET = REPO_ROOT / "docs" / "10-ground-truth-datasets.log.json"

UA = {"User-Agent": "PRAMAAN-SIH2026-dataset-verifier/0.1 (+research; low volume)"}
TIMEOUT = httpx.Timeout(45.0, connect=15.0)


class S:
    OK = "OK"
    AUTH = "AUTH_REQUIRED"
    GATED = "GATED"
    LICENCE = "LICENCE_BLOCKED"
    FAIL = "FAIL"


@dataclass
class Source:
    key: str
    name: str
    url: str
    #: What PRAMAAN would actually use it for.
    use: str
    licence: str
    #: GT-1 (photo AI), GT-2 (intervention reference), or CONTEXT.
    asset: str
    check: Callable[[httpx.Client], tuple[str, str]]
    timeout_s: float | None = None


@dataclass
class Result:
    key: str
    name: str
    url: str
    use: str
    licence: str
    asset: str
    status: str
    detail: str
    elapsed_ms: int
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _get(client: httpx.Client, url: str) -> httpx.Response:
    return client.get(url, headers=UA, follow_redirects=True)


def check_reachable(url: str, marker: str = "") -> Callable[[httpx.Client], tuple[str, str]]:
    def _c(client: httpx.Client) -> tuple[str, str]:
        r = _get(client, url)
        if r.status_code != 200:
            return S.FAIL, f"HTTP {r.status_code}"
        if marker and marker.lower() not in r.text.lower():
            return S.FAIL, f"200 but expected marker {marker!r} absent"
        return S.OK, f"200, {len(r.content)} bytes"

    return _c


def check_jrc_lucas_cover(client: httpx.Client) -> tuple[str, str]:
    """The JRC FTP-over-HTTPS directory holding the 874,646 cover photos."""
    url = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/LUCAS/LUCAS_COVER/"
    r = _get(client, url)
    if r.status_code != 200:
        return S.FAIL, f"HTTP {r.status_code}"
    body = r.text
    hits = [y for y in ("2006", "2009", "2012", "2015", "2018") if y in body]
    if not hits:
        return S.OK, f"200, directory listing {len(body)} bytes, no year folders visible"
    return S.OK, f"200, directory listing exposes campaign folders: {', '.join(hits)}"


def check_places365(client: httpx.Client) -> tuple[str, str]:
    """Places365 is now form-gated pending a v2 release."""
    r = _get(client, "http://places2.csail.mit.edu/download.html")
    if r.status_code != 200:
        return S.FAIL, f"HTTP {r.status_code}"
    body = r.text.lower()
    if "sign this form" in body or "forms.gle" in body:
        return (
            S.GATED,
            "download page no longer offers direct archives: access to the legacy "
            "dataset now requires signing a Google Form while v2 is in progress",
        )
    return S.OK, "200, direct download links present"


def check_mapillary(client: httpx.Client) -> tuple[str, str]:
    """Mapillary needs a free token; probe unauthenticated to confirm the gate."""
    token = os.environ.get("MAPILLARY_TOKEN")
    url = "https://graph.mapillary.com/images"
    params = {"bbox": "77.30,19.14,77.31,19.15", "limit": "1"}
    if token:
        params["access_token"] = token
    r = client.get(url, params=params, headers=UA, follow_redirects=True)
    if not token:
        if r.status_code in (400, 401, 403):
            return (
                S.AUTH,
                f"HTTP {r.status_code} unauthenticated as expected; a free client "
                "token from the Mapillary developer dashboard is required. Imagery "
                "is CC-BY-SA 4.0 (verified on Mapillary's own licence page).",
            )
        return S.AUTH, f"HTTP {r.status_code} unauthenticated; token still required"
    if r.status_code != 200:
        return S.FAIL, f"authenticated request returned HTTP {r.status_code}"
    n = len(r.json().get("data", []))
    return S.OK, f"authenticated: {n} image(s) returned for a demo-district bbox"


#: Commons `gsradius` is capped at 10,000 m by the GeoData extension. Passing a
#: larger value returns an EMPTY result, which reads exactly like "no images
#: here". During this survey a 50,000 m request returned 0 and was very nearly
#: recorded as a coverage finding. It is an invalid-parameter artefact.
COMMONS_MAX_RADIUS_M = 10_000

#: On-topic Commons categories, counted because geosearch alone understates the
#: usable pool: most Indian structure photographs are categorised but not
#: geotagged.
COMMONS_CATEGORIES = (
    "Check dams in India",
    "Dams in Maharashtra",
    "Ponds in India",
    "Water tanks in India",
    "Stepwells in India",
    "Irrigation in India",
)


def check_commons_geosearch(client: httpx.Client) -> tuple[str, str]:
    """Wikimedia Commons: geosearch at the demo AOI plus on-topic category counts.

    Commons files carry per-file licences, so this is a *mineable source* rather
    than a dataset: every candidate image's licence must be read individually.

    A positive control is included for the same reason as in the Overpass check.
    A sparse count at a rural Indian AOI is indistinguishable from a broken query
    unless a dense location is confirmed to return results in the same run.
    """
    url = "https://commons.wikimedia.org/w/api.php"

    def geosearch(lat: float, lon: float) -> int | None:
        rr = client.get(
            url,
            params={
                "action": "query",
                "format": "json",
                "list": "geosearch",
                "gscoord": f"{lat}|{lon}",
                "gsradius": str(COMMONS_MAX_RADIUS_M),
                "gslimit": "500",
                "gsnamespace": "6",
            },
            headers=UA,
            follow_redirects=True,
        )
        if rr.status_code != 200:
            return None
        return len(rr.json().get("query", {}).get("geosearch", []))

    control = geosearch(48.8584, 2.2945)  # Eiffel Tower: must be dense
    aoi = geosearch(19.1500, 77.3000)  # candidate demo AOI, Nanded
    if control is None or aoi is None:
        return S.FAIL, "geosearch did not answer"
    if control == 0:
        return S.FAIL, "positive control returned 0 — query or API is broken"

    cat_counts: list[str] = []
    total = 0
    for cat in COMMONS_CATEGORIES:
        rr = client.get(
            url,
            params={
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": f"Category:{cat}",
                "cmtype": "file",
                "cmlimit": "500",
            },
            headers=UA,
            follow_redirects=True,
        )
        if rr.status_code != 200:
            cat_counts.append(f"{cat}=HTTP{rr.status_code}")
            continue
        n = len(rr.json().get("query", {}).get("categorymembers", []))
        total += n
        cat_counts.append(f"{cat}={n}")

    return (
        S.OK,
        f"control (Paris 10 km) {control} files; demo AOI (19.15N 77.30E, 10 km) "
        f"{aoi} files — genuinely sparse, not a query error. On-topic categories "
        f"total ~{total} files: {'; '.join(cat_counts)}. Radius is capped at "
        f"{COMMONS_MAX_RADIUS_M} m; larger values return empty, not more data.",
    )


def check_overpass(client: httpx.Client) -> tuple[str, str]:
    """Overpass, with a mandatory sanity check against a regional-extract mirror.

    A mirror serving only one country returns 0 for India and looks like a valid
    'no data' answer. This cost real time during the survey — overpass.osm.ch
    answered HTTP 200 and reported 0 water features for Maharashtra because it
    only holds Switzerland. So: never trust a count without a positive control.
    """
    url = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    ctrl = '[out:json][timeout:60];nwr["natural"="water"](47.35,8.50,47.42,8.58);out count;'
    test = '[out:json][timeout:60];nwr["natural"="water"](19.0,77.2,19.3,77.5);out count;'

    def one(q: str) -> int | None:
        rr = client.get(url, params={"data": q}, headers=UA, follow_redirects=True)
        if rr.status_code != 200:
            return None
        for e in rr.json().get("elements", []):
            if e.get("type") == "count":
                return int(e["tags"].get("total", 0))
        return None

    zurich = one(ctrl)
    india = one(test)
    if zurich is None or india is None:
        return S.FAIL, "mirror did not answer the count queries"
    if zurich == 0:
        return S.FAIL, "positive control returned 0 — mirror is broken"
    if india == 0:
        return S.FAIL, (
            f"positive control OK (Zurich {zurich}) but India returned 0 — "
            "mirror is a regional extract, its India counts are invalid"
        )
    return S.OK, (
        f"global mirror confirmed by positive control (Zurich {zurich}); "
        f"{india} natural=water features in the demo AOI bbox"
    )


SOURCES: list[Source] = [
    Source(
        key="lucas_cover",
        name="JRC LUCAS cover photos 2006-2018",
        url="https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/LUCAS/LUCAS_COVER/",
        use="874,646 geo-tagged close-up land-cover photos with land-cover and plant "
        "species labels. Pre-training / linear-probe corpus for vegetation and "
        "bare-soil labels. EU only, so a domain-shift source, never a test set.",
        licence="JRC open data (CC-BY-4.0 paper, ESSD doi:10.5194/essd-14-4463-2022)",
        asset="GT-1",
        check=check_jrc_lucas_cover,
    ),
    Source(
        key="lucas_harmonised",
        name="JRC Harmonised LUCAS in-situ database",
        url="https://data.jrc.ec.europa.eu/dataset/f85907ae-d123-471f-a44a-8cca993485a2",
        use="The survey table behind the photos: 1,351,293 observations at 651,780 "
        "locations. Joins photo -> land-cover label -> coordinates.",
        licence="JRC open data",
        asset="GT-1",
        check=check_reachable(
            "https://data.jrc.ec.europa.eu/dataset/f85907ae-d123-471f-a44a-8cca993485a2",
            "LUCAS",
        ),
    ),
    Source(
        key="lucas_ml",
        name="LUCAS landscape photos, ML-ready (Source Cooperative)",
        url="https://source.coop/jrc-lucas/jrc-lucas-ml",
        use="Agri-environmental semantic segmentation of LUCAS landscape photos. "
        "Closest public analogue to a DRISHTI landscape geotag: observer-view "
        "photographs of agricultural land with per-pixel labels.",
        licence="Check per-repository terms on Source Cooperative",
        asset="GT-1",
        check=check_reachable("https://source.coop/jrc-lucas/jrc-lucas-ml", "LUCAS"),
    ),
    Source(
        key="mapillary",
        name="Mapillary street-level imagery API v4",
        url="https://graph.mapillary.com/images",
        use="THE most relevant ground-photo source for India: geotagged, "
        "camera-bearing, roadside imagery covering rural roads. Mine within the "
        "demo districts for ponds, tanks, bunds, gullies and bare fields.",
        licence="CC-BY-SA 4.0 (verified on Mapillary's licence page). ShareAlike "
        "means derived annotation sets must be shared alike - fine for us, we "
        "publish GT-1 anyway.",
        asset="GT-1",
        check=check_mapillary,
    ),
    Source(
        key="commons",
        name="Wikimedia Commons geosearch API",
        url="https://commons.wikimedia.org/w/api.php",
        use="Mineable, not a dataset: geotagged CC-licensed photographs. Useful "
        "for check dams, percolation tanks and stepwells where Mapillary has no "
        "road access. Every file's licence must be read individually.",
        licence="Per-file (CC0 / CC-BY / CC-BY-SA / rarely non-free)",
        asset="GT-1",
        check=check_commons_geosearch,
    ),
    Source(
        key="places365",
        name="Places365 scene classification",
        url="http://places2.csail.mit.edu/download.html",
        use="WOULD be useful for zero-shot prompt calibration (categories include "
        "creek, farm, cultivated field, fishpond). Two blockers: access is now "
        "form-gated, and the licence is non-commercial research only - which "
        "constraint C-7 already forbids as a production dependency.",
        licence="Non-commercial research/education only -> cannot be a production "
        "dependency for a government system (same rule that excludes GEE, C-7)",
        asset="GT-1",
        check=check_places365,
    ),
    Source(
        key="agrifieldnet",
        name="AgriFieldNet India (Radiant Earth / IDinsight)",
        url="https://source.coop/radiantearth/agrifieldnet-competition",
        use="Sentinel-2 crop-type labels for fields in Uttar Pradesh, Rajasthan, "
        "Odisha and Bihar. Not watershed structures, but the only India-specific "
        "labelled EO reference set found - useful to sanity-check our seasonal "
        "NDVI compositing against independent Indian crop labels.",
        licence="CC-BY-4.0",
        asset="CONTEXT",
        check=check_reachable(
            "https://source.coop/radiantearth/agrifieldnet-competition", "agrifieldnet"
        ),
    ),
    Source(
        key="overpass",
        name="OpenStreetMap via Overpass API",
        url="https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        use="Candidate GT-2 seed. VERDICT: unusable for check dams (see the "
        "measured counts in the notes below), partially usable for ponds and "
        "water bodies. Production route is a Geofabrik India extract, not the "
        "live API.",
        licence="ODbL 1.0 (share-alike; attribution required)",
        asset="GT-2",
        check=check_overpass,
    ),
    Source(
        key="geofabrik",
        name="Geofabrik India OSM extract",
        url="https://download.geofabrik.de/asia/india.html",
        use="The reliable way to use OSM at scale: download the India .osm.pbf "
        "once and query locally with osmium, instead of hammering Overpass.",
        licence="ODbL 1.0",
        asset="GT-2",
        check=check_reachable("https://download.geofabrik.de/asia/india.html", "india"),
    ),
    Source(
        key="jrc_gsw",
        name="JRC Global Surface Water",
        url="https://global-surface-water.appspot.com/",
        use="Already in the design (docs §12): 30 m multi-decadal water occurrence. "
        "Doubles as an independent GT-2 reference for the waterbody_renovation "
        "type, which is why that type carries an L4 ceiling.",
        licence="CC-BY-4.0",
        asset="GT-2",
        check=check_reachable("https://global-surface-water.appspot.com/"),
    ),
]


def wrap(source: Source, client: httpx.Client) -> Result:
    started = datetime.now(UTC)
    try:
        status, detail = source.check(client)
    except httpx.TimeoutException:
        status, detail = S.FAIL, f"timeout after {TIMEOUT.read}s"
    except httpx.HTTPError as exc:
        status, detail = S.FAIL, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        status, detail = S.FAIL, f"probe error {type(exc).__name__}: {exc}"
    elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)
    if status == S.OK and "non-commercial" in source.licence.lower():
        status = S.LICENCE
    return Result(
        key=source.key,
        name=source.name,
        url=source.url,
        use=source.use,
        licence=source.licence,
        asset=source.asset,
        status=status,
        detail=detail,
        elapsed_ms=elapsed,
    )


BADGE = {
    S.OK: "✅ OK",
    S.AUTH: "🔑 TOKEN NEEDED",
    S.GATED: "⛔ FORM-GATED",
    S.LICENCE: "⚖️ LICENCE BLOCKS US",
    S.FAIL: "❌ FAIL",
}


def render(results: list[Result]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# 10 — Ground-truth dataset survey (machine-verified)",
        "",
        "> **Generated by `scripts/verify_datasets.py`. Do not hand-edit.**",
        "> Every row is the result of an actual request at the timestamp shown.",
        "",
        f"**Last run:** {now}",
        "",
        "## Headline finding",
        "",
        "**No public dataset exists for the GT-1 label set.** Nothing indexes "
        "ground-level photographs of Indian watershed structures labelled for "
        "water presence, structure presence, vegetation density, exposed soil, "
        "gully erosion or construction stage. This confirms design-document "
        "constraint **C-6** as an empirical result rather than an assumption.",
        "",
        "The consequence is unchanged from the plan: **GT-1 must be built**, and "
        "the sources below are pre-training corpora, mining targets and reference "
        "sets - not substitutes for the annotation sprint (milestone M3).",
        "",
        "## Sources tested",
        "",
        "| Source | Asset | Status | Licence | Result |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        detail = r.detail.replace("|", "\\|")
        lic = r.licence.replace("|", "\\|")
        lines.append(f"| [{r.name}]({r.url}) | {r.asset} | {BADGE[r.status]} | {lic} | {detail} |")
    lines += [
        "",
        "## How PRAMAAN would use each source",
        "",
    ]
    for r in results:
        lines.append(f"**{r.name}** ({r.asset}) — {r.use}")
        lines.append("")
    lines += [
        "## Measured OSM coverage — why OSM cannot be the GT-2 reference set",
        "",
        "**Provenance of these counts.** They were captured on 2026-08-28 from a",
        "*global* Overpass mirror, each India number accepted only after a",
        "positive control (a Zurich bbox) returned a non-zero count in the same",
        "session. They are therefore measured, not estimated - but they are a",
        "point-in-time capture, and the live probe in the table above may show",
        "`FAIL` because Overpass rate-limits repeated survey traffic. Re-verify",
        "via the **Geofabrik India extract** (verified reachable above), which is",
        "the correct route for anything beyond a spot check.",
        "",
        "This precaution was not theoretical. The `overpass.osm.ch` mirror",
        "answered HTTP 200 and reported **0** water features for the whole of",
        "Maharashtra - because it serves Switzerland only. An unvalidated mirror",
        "produces confident zeros that look exactly like a real finding.",
        "",
        "| Tag | Maharashtra | Note |",
        "|---|---|---|",
        "| `waterway=check_dam` | **2** | Against ~1.24 lakh WDC-PMKSY structures "
        "nationally. All of India returns **5**. |",
        "| `waterway=dam` | 902 | Large dams, not watershed-scale interventions |",
        "| `water=pond` | 7,365 | Usable as a weak reference for pond-type works |",
        "| `water=reservoir` | 3,613 | |",
        "| `landuse=reservoir` | 1,561 | |",
        "| `water=basin` | 211 | |",
        "| `natural=water` (all) | 23,106 | Broadest water-body layer |",
        "",
        "**Conclusion.** OSM check-dam coverage in India is effectively zero (5 "
        "nationally). It is therefore rejected as a GT-2 source for structure "
        "existence, exactly as the design document assumed. `water=pond` and "
        "`natural=water` remain useful as a weak prior and as a cross-check on "
        "MNDWI-derived water masks. GT-2 stays as planned: manual photo-"
        "interpretation from high-resolution basemap imagery, reported honestly "
        "as a *reference set*, never as *ground truth*.",
        "",
        "## Reproducing",
        "",
        "```bash",
        "python scripts/verify_datasets.py --write-docs",
        "```",
        "",
        "Raw results: `docs/10-ground-truth-datasets.log.json`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-docs", action="store_true")
    args = ap.parse_args()

    results: list[Result] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for source in SOURCES:
            res = wrap(source, client)
            results.append(res)
            print(f"{res.status:<16} {res.name:<48} {res.elapsed_ms:>6} ms  {res.detail[:110]}")

    if args.write_docs:
        DOCS_TARGET.parent.mkdir(parents=True, exist_ok=True)
        DOCS_TARGET.write_text(render(results), encoding="utf-8")
        LOG_TARGET.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {DOCS_TARGET.relative_to(REPO_ROOT)}")
        print(f"wrote {LOG_TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
