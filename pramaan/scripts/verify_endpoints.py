#!/usr/bin/env python3
"""Verify every external data endpoint PRAMAAN depends on, and write the result
to docs/09-data-sources.md as a machine-generated test log.

Why this file exists (docs §38.9, §29): the submission's entire honesty posture
rests on `docs/09-data-sources.md` being a *record of tests we ran*, not a list
of URLs we found. This script turns "we verified the APIs" from a claim into a
runnable, CI-scheduled artefact.

Design rules:
- Read-only. Never downloads bulk data; issues the cheapest request that proves
  the endpoint answers (GetCapabilities, a 1-item STAC search, an auth probe).
- Never fails the build on an endpoint being down. A down endpoint is a
  *finding to record*, not a broken commit. Exit code is non-zero only if the
  script itself could not run (e.g. no network at all) or --strict is passed.
- Credentials come from the environment and are never logged. Endpoints that
  require credentials we do not hold report `SKIPPED_NO_CREDENTIALS`, which is
  an honest and defensible status.

Usage:
    python scripts/verify_endpoints.py                 # test, print table
    python scripts/verify_endpoints.py --write-docs    # also rewrite docs/09
    python scripts/verify_endpoints.py --strict        # non-zero exit on FAIL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_TARGET = REPO_ROOT / "docs" / "09-data-sources.md"
LOG_TARGET = REPO_ROOT / "docs" / "09-data-sources.log.json"

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
UA = {"User-Agent": "PRAMAAN-endpoint-verifier/0.1 (SIH 2026 prototype; contact: team)"}


class Status:
    OK = "OK"
    FAIL = "FAIL"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED_NO_CREDENTIALS"


@dataclass
class Probe:
    """One endpoint test.

    `check` returns (status, detail). It must not raise; wrap_probe handles that.
    """

    key: str
    name: str
    purpose: str
    url: str
    licence: str
    check: Callable[[httpx.Client], tuple[str, str]]
    needs_env: tuple[str, ...] = ()
    # Some government endpoints are legitimately slow — Bhuvan's WMS
    # GetCapabilities is a ~7 MB document (measured, see docs/09) and cannot
    # answer inside the default budget. A per-probe override keeps the default
    # tight for everything else instead of globally weakening the timeout.
    timeout_s: float | None = None


@dataclass
class Result:
    key: str
    name: str
    purpose: str
    url: str
    licence: str
    status: str
    detail: str
    elapsed_ms: int
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# --------------------------------------------------------------------------
# Individual checks. Each one asks the cheapest question that proves liveness.
# --------------------------------------------------------------------------


def _stac_search(client: httpx.Client, url: str, collection: str) -> tuple[str, str]:
    """POST a 1-item STAC search. Proves the API answers and the collection exists."""
    body: dict[str, Any] = {
        "collections": [collection],
        # Small AOI in the candidate demo region (Marathwada, Maharashtra).
        "bbox": [76.0, 18.0, 76.5, 18.5],
        "datetime": "2024-01-01T00:00:00Z/2024-12-31T23:59:59Z",
        "limit": 1,
    }
    r = client.post(url, json=body, headers=UA)
    if r.status_code != 200:
        return Status.FAIL, f"HTTP {r.status_code}: {r.text[:160]}"
    payload = r.json()
    n = len(payload.get("features", []))
    matched = payload.get("numberMatched", payload.get("context", {}).get("matched"))
    if n == 0:
        return Status.DEGRADED, f"API answered 200 but returned 0 items (matched={matched})"
    item = payload["features"][0]
    sample = str(item.get("id", "?"))[:60]
    return Status.OK, f"1 item returned (matched={matched}), sample id={sample}"


def check_cmr_stac_hls(client: httpx.Client) -> tuple[str, str]:
    return _stac_search(client, "https://cmr.earthdata.nasa.gov/stac/LPCLOUD/search", "HLSS30_2.0")


def check_cdse_stac(client: httpx.Client) -> tuple[str, str]:
    # Collection id verified by enumerating /stac/collections: the id is
    # `sentinel-2-l2a` (lowercase). `SENTINEL-2` — the obvious guess, and what
    # an earlier draft of this script used — returns HTTP 400
    # CollectionInQuerryDoesNotExist. Exactly the class of assumption this
    # script exists to catch.
    return _stac_search(
        client, "https://catalogue.dataspace.copernicus.eu/stac/search", "sentinel-2-l2a"
    )


def check_element84_stac(client: httpx.Client) -> tuple[str, str]:
    """Independent second Sentinel-2 source (FR-5.1 requires >= 2 sources)."""
    return _stac_search(
        client, "https://earth-search.aws.element84.com/v1/search", "sentinel-2-l2a"
    )


def check_ogc_capabilities(url: str, layer_hint: str) -> Callable[[httpx.Client], tuple[str, str]]:
    def _check(client: httpx.Client) -> tuple[str, str]:
        r = client.get(
            url,
            params={"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"},
            headers=UA,
        )
        if r.status_code != 200:
            return Status.FAIL, f"HTTP {r.status_code}"
        text = r.text
        if "WMS_Capabilities" not in text and "WMT_MS_Capabilities" not in text:
            return Status.FAIL, f"200 but body is not WMS capabilities XML ({len(text)} bytes)"
        n_layers = text.count("<Layer")
        hint = "found" if layer_hint.lower() in text.lower() else "NOT found"
        mb = len(r.content) / 1_048_576
        return (
            Status.OK,
            f"capabilities XML {mb:.2f} MB, ~{n_layers} <Layer> nodes; "
            f"'{layer_hint}' {hint} — too large to fetch per-request, cache at "
            f"district onboarding",
        )

    return _check


def check_bhoonidhi_auth(client: httpx.Client) -> tuple[str, str]:
    """Bhoonidhi requires a registered account; JWT bearer flow [VERIFIED in docs §12].

    We hold no government credential, so without env vars this is an honest SKIP
    rather than a failure. With credentials, it probes the token endpoint only.
    """
    user = os.environ.get("BHOONIDHI_USER")
    pwd = os.environ.get("BHOONIDHI_PASS")
    if not user or not pwd:
        return (
            Status.SKIPPED,
            "no BHOONIDHI_USER/BHOONIDHI_PASS in env; documented as a "
            "production-only source with an SIH substitute (docs §12.5)",
        )
    r = client.post(
        "https://bhoonidhi.nrsc.gov.in/bhoonidhi/authenticate",
        json={"username": user, "password": pwd},
        headers=UA,
    )
    if r.status_code != 200:
        return Status.FAIL, f"auth HTTP {r.status_code}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        return Status.OK, "auth returned a token"
    return Status.DEGRADED, "200 but no token key"


def check_plain_get(url: str, expect: str = "") -> Callable[[httpx.Client], tuple[str, str]]:
    def _check(client: httpx.Client) -> tuple[str, str]:
        r = client.get(url, headers=UA, follow_redirects=True)
        if r.status_code != 200:
            return Status.FAIL, f"HTTP {r.status_code}"
        if expect and expect.lower() not in r.text.lower():
            return Status.DEGRADED, f"200 but expected marker '{expect}' absent"
        return Status.OK, f"200, {len(r.content)} bytes"

    return _check


PROBES: list[Probe] = [
    Probe(
        key="hls",
        name="NASA HLS 30 m (CMR STAC, LPCLOUD)",
        purpose="Primary harmonised 30 m optical series (L30+S30) — the PS's own resolution tier",
        url="https://cmr.earthdata.nasa.gov/stac/LPCLOUD",
        licence="Public domain (US Govt)",
        check=check_cmr_stac_hls,
    ),
    Probe(
        key="cdse",
        name="Copernicus Data Space (STAC)",
        purpose="Sentinel-2 L2A — independent second optical source (FR-5.1)",
        url="https://catalogue.dataspace.copernicus.eu/stac",
        licence="Copernicus open licence",
        check=check_cdse_stac,
    ),
    Probe(
        key="earthsearch",
        name="Element84 Earth Search (STAC)",
        purpose="Sentinel-2 L2A mirror — fallback if CDSE throttles (risk R-01)",
        url="https://earth-search.aws.element84.com/v1",
        licence="Copernicus open licence",
        check=check_element84_stac,
    ),
    Probe(
        key="bhuvan_wms",
        name="Bhuvan OGC WMS (NRSC)",
        purpose="Authoritative LULC 50K / wasteland / erosion thematic layers "
        "— consumed, not re-derived",
        url="https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms",
        licence="NRSC terms; view/overlay use",
        check=check_ogc_capabilities("https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms", "lulc"),
        # Measured 2026-08: this endpoint returns ~7 MB of capabilities XML and
        # needs ~30 s. `bhuvan-vec1` times out entirely and `bhuvan-ras2` /
        # `bhuvan-app1` return 404 for this path — vec2 is the one that works.
        # This is risk R-07 (endpoint availability) with a measurement attached.
        timeout_s=90.0,
    ),
    Probe(
        key="bhoonidhi",
        name="NRSC Bhoonidhi API",
        purpose="Resourcesat / Indian sensors; open >5 m under Indian Space Policy 2023",
        url="https://bhoonidhi.nrsc.gov.in/",
        licence="NRSC / ISP-2023; >5 m open, finer is govt-only (constraint C-2)",
        check=check_bhoonidhi_auth,
        needs_env=("BHOONIDHI_USER", "BHOONIDHI_PASS"),
    ),
    Probe(
        key="jrc_gsw",
        name="JRC Global Surface Water",
        purpose="30 m independent water-occurrence history — validates our MNDWI/Otsu water masks",
        url="https://global-surface-water.appspot.com/",
        licence="CC BY 4.0",
        check=check_plain_get("https://global-surface-water.appspot.com/"),
    ),
    Probe(
        key="chirps",
        name="CHIRPS rainfall (UCSB CHG)",
        purpose="Gridded rainfall for the context family's rainfall normalisation (§17.3)",
        url="https://data.chc.ucsb.edu/products/CHIRPS-2.0/",
        licence="Public, CHG terms",
        check=check_plain_get("https://data.chc.ucsb.edu/products/CHIRPS-2.0/", "global_monthly"),
    ),
    Probe(
        key="nasadem",
        name="NASADEM / SRTM (CMR STAC)",
        purpose="30 m DEM fallback where CartoDEM is unavailable to non-govt entities",
        url="https://cmr.earthdata.nasa.gov/stac/LPCLOUD",
        licence="Public domain (US Govt)",
        check=check_plain_get("https://cmr.earthdata.nasa.gov/stac/LPCLOUD", "links"),
    ),
]


def wrap_probe(probe: Probe, client: httpx.Client) -> Result:
    started = datetime.now(UTC)
    budget = probe.timeout_s if probe.timeout_s is not None else TIMEOUT.read
    try:
        if probe.timeout_s is None:
            status, detail = probe.check(client)
        else:
            # A fresh client so the override cannot leak into other probes.
            with httpx.Client(
                timeout=httpx.Timeout(probe.timeout_s, connect=20.0), follow_redirects=True
            ) as slow_client:
                status, detail = probe.check(slow_client)
    except httpx.TimeoutException:
        status, detail = Status.FAIL, f"timeout after {budget}s"
    except httpx.HTTPError as exc:
        status, detail = Status.FAIL, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — a probe bug must not abort the run
        status, detail = Status.FAIL, f"probe error {type(exc).__name__}: {exc}"
    elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)
    return Result(
        key=probe.key,
        name=probe.name,
        purpose=probe.purpose,
        url=probe.url,
        licence=probe.licence,
        status=status,
        detail=detail,
        elapsed_ms=elapsed,
    )


def render_markdown(results: list[Result]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    tracked = (Status.OK, Status.DEGRADED, Status.FAIL, Status.SKIPPED)
    counts = {s: sum(1 for r in results if r.status == s) for s in tracked}
    lines = [
        "# 09 — Data Sources (machine-verified)",
        "",
        "> **This file is generated by `scripts/verify_endpoints.py`. Do not hand-edit.**",
        "> Every row is the result of an actual request issued at the timestamp shown.",
        "> A `FAIL` row is left in place deliberately — recording what did not work is",
        "> the point of this file (docs §29 working agreements).",
        "",
        f"**Last run:** {now}  ",
        f"**Summary:** {counts[Status.OK]} OK · {counts[Status.DEGRADED]} DEGRADED · "
        f"{counts[Status.FAIL]} FAIL · {counts[Status.SKIPPED]} skipped (no credentials)",
        "",
        "| Source | Status | Purpose | Licence | Detail | ms |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        badge = {
            Status.OK: "✅ OK",
            Status.DEGRADED: "⚠️ DEGRADED",
            Status.FAIL: "❌ FAIL",
            Status.SKIPPED: "⏭️ NO CREDS",
        }[r.status]
        detail = r.detail.replace("|", "\\|")
        lines.append(
            f"| [{r.name}]({r.url}) | {badge} | {r.purpose} | {r.licence} "
            f"| {detail} | {r.elapsed_ms} |"
        )
    lines += [
        "",
        "## How to read this table",
        "",
        "- **OK** — endpoint answered and returned usable content on this run.",
        "- **DEGRADED** — endpoint answered but the response was not what we need "
        "(e.g. 0 items matched). Treated as a risk, not a success.",
        "- **FAIL** — endpoint did not answer. The mitigation is in docs §27 "
        "(risk register) and the substitution plan in §12.",
        "- **NO CREDS** — requires a government credential we do not hold as a "
        "non-departmental team. This is a stated constraint (C-1, C-2), not an "
        "oversight; the SIH substitute is documented in §12.5.",
        "",
        "## Reproducing",
        "",
        "```bash",
        "make verify-endpoints          # print the table",
        "python scripts/verify_endpoints.py --write-docs   # regenerate this file",
        "```",
        "",
        "Raw machine-readable results: `docs/09-data-sources.log.json`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-docs", action="store_true", help="rewrite docs/09-data-sources.md")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any probe FAILs")
    args = ap.parse_args()

    results: list[Result] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for probe in PROBES:
            res = wrap_probe(probe, client)
            results.append(res)
            print(f"{res.status:<24} {res.name:<40} {res.elapsed_ms:>6} ms  {res.detail}")

    if args.write_docs:
        DOCS_TARGET.parent.mkdir(parents=True, exist_ok=True)
        DOCS_TARGET.write_text(render_markdown(results), encoding="utf-8")
        LOG_TARGET.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {DOCS_TARGET.relative_to(REPO_ROOT)}")
        print(f"wrote {LOG_TARGET.relative_to(REPO_ROOT)}")

    n_fail = sum(1 for r in results if r.status == Status.FAIL)
    if args.strict and n_fail:
        print(f"\n--strict: {n_fail} endpoint(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
