#!/usr/bin/env python3
"""Reproducible performance benchmarks for the deterministic core.

Referenced by docs §30 as `scripts/benchmark.py`. Covers what can be measured
without network or bulk data: the reconciliation engine and the pure producers.
The network- and data-dependent measurements (imagery throughput, CPU inference,
hydrology) live in `docs/11-feasibility.md` with their methods recorded, because
they need multi-GB downloads and a model checkpoint that CI should not fetch.

Usage:
    python scripts/benchmark.py            # human-readable
    python scripts/benchmark.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "tests"))

from conftest import bundle, fam, gates  # noqa: E402

from app.services.reconcile import EngineConfig, reconcile  # noqa: E402
from app.services.reconcile.types import FAMILIES  # noqa: E402
from app.services.terrain import detectability, plausibility  # noqa: E402
from app.services.terrain.types import DiskStat, TerrainSample  # noqa: E402

#: Programme scale, from the design document's own figures.
WORKS_PER_DISTRICT = 1_200
DISTRICTS_PER_STATE = 40
WDC_PMKSY_STRUCTURES = 124_000


def synth_bundles(n: int, seed: int = 1) -> list:  # type: ignore[type-arg]
    rng = random.Random(seed)
    types = ["check_dam", "farm_pond", "plantation", "contour_trench", "dug_well"]
    out = []
    for i in range(n):
        out.append(
            bundle(
                claim_id=f"BENCH-{i}",
                intervention_type=rng.choice(types),
                families=tuple(
                    fam(f, rng.uniform(-1, 1), available=rng.random() > 0.15) for f in FAMILIES
                ),
                metadata_integrity=rng.uniform(0.3, 1.0),
                data_sufficiency=rng.uniform(0.1, 1.0),
                gate=gates(passed=rng.random() > 0.4, escalated=rng.random() > 0.5),
            )
        )
    return out


def bench_engine(n: int = 5000) -> dict[str, object]:
    cfg = EngineConfig()
    bundles = synth_bundles(n)
    for b in bundles[:50]:
        reconcile(b, cfg)  # warm
    t0 = time.perf_counter()
    verdicts = [reconcile(b, cfg) for b in bundles]
    dt = time.perf_counter() - t0

    per_us = dt / n * 1e6
    levels = Counter(v.level.value for v in verdicts)
    return {
        "verdicts": n,
        "seconds": round(dt, 4),
        "us_per_verdict": round(per_us, 1),
        "verdicts_per_second": round(n / dt),
        "district_ms": round(WORKS_PER_DISTRICT * per_us / 1000, 1),
        "state_s": round(DISTRICTS_PER_STATE * WORKS_PER_DISTRICT * per_us / 1e6, 2),
        "national_s": round(WDC_PMKSY_STRUCTURES * per_us / 1e6, 2),
        "level_distribution": dict(levels.most_common()),
        # Invariants must hold across the whole random sweep, not just in tests.
        "all_dissent_non_empty": all(v.dissent for v in verdicts),
        "invariant_i1_holds": all(v.confidence <= abs(v.score) + 1e-9 for v in verdicts),
        # N3 should be unreachable without an excluded alternative. A non-zero
        # count here on random input would mean contradictions are reachable by
        # accident, which is the failure the two named paths exist to prevent.
        "n3_on_random_input": levels.get("N3_contradicted", 0),
    }


def bench_producers(n: int = 5000) -> dict[str, object]:
    rng = random.Random(7)
    samples = []
    for _ in range(n):
        slope = rng.uniform(0.2, 25.0)
        samples.append(
            TerrainSample(
                disk_radius_m=15.0,
                slope_deg=DiskStat(slope * 0.8, slope, slope * 1.2),
                strahler_order=DiskStat(0, rng.randint(0, 4), 5),
                flow_accumulation_px=DiskStat(1, rng.uniform(1, 9000), 12000),
                dist_to_stream_m=DiskStat(0, rng.uniform(0, 600), 900),
                upstream_area_km2=DiskStat(0.0, rng.uniform(0, 5), 8.0),
                in_depression=rng.random() > 0.5,
                dem_product="NASADEM",
                dem_version="001",
                stream_threshold_px=100.0,
                stream_threshold_agreement=0.79,
            )
        )
    types = ["check_dam", "farm_pond", "plantation", "contour_trench", "gully_plug"]

    t0 = time.perf_counter()
    for i, s in enumerate(samples):
        plausibility.evaluate(types[i % len(types)], s)
    plaus_dt = time.perf_counter() - t0

    t1 = time.perf_counter()
    for i in range(n):
        detectability.evaluate(types[i % len(types)], cluster_member_count=4)
    gate_dt = time.perf_counter() - t1

    return {
        "n": n,
        "plausibility_us": round(plaus_dt / n * 1e6, 1),
        "detectability_us": round(gate_dt / n * 1e6, 1),
        "combined_district_ms": round(WORKS_PER_DISTRICT * (plaus_dt + gate_dt) / n * 1000, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--n", type=int, default=5000)
    args = ap.parse_args()

    engine = bench_engine(args.n)
    producers = bench_producers(args.n)
    payload = {"engine": engine, "producers": producers}

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("=== Reconciliation engine (pure, no IO) ===")
    print(f"  {engine['verdicts']} verdicts in {engine['seconds']}s")
    print(f"  {engine['us_per_verdict']} us/verdict ({engine['verdicts_per_second']:,} verdicts/s)")
    print(f"  one district ({WORKS_PER_DISTRICT:,} works): {engine['district_ms']} ms")
    print(f"  one state ({DISTRICTS_PER_STATE} districts):   {engine['state_s']} s")
    print(f"  national ({WDC_PMKSY_STRUCTURES:,} structures): {engine['national_s']} s")
    print("\n  level distribution on random evidence:")
    for level, count in engine["level_distribution"].items():  # type: ignore[union-attr]
        pct = 100 * count / engine["verdicts"]  # type: ignore[operator]
        print(f"    {level:<26} {count:>6} ({pct:4.1f}%)")
    print(f"\n  dissent always non-empty : {engine['all_dissent_non_empty']}")
    print(f"  invariant I1 holds       : {engine['invariant_i1_holds']}")
    print(
        f"  N3 on random input       : {engine['n3_on_random_input']} "
        "(must be 0 — contradictions are unreachable without an excluded "
        "alternative)"
    )

    print("\n=== Pure producers ===")
    print(f"  terrain plausibility : {producers['plausibility_us']} us/claim")
    print(f"  detectability gate   : {producers['detectability_us']} us/claim")
    print(
        f"  both, one district   : {producers['combined_district_ms']} ms "
        f"for {WORKS_PER_DISTRICT:,} works"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
