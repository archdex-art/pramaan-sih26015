#!/usr/bin/env python3
"""Seed the golden-case suite into the register as badged synthetic claims.

## Why the register needs these

The claims register with one row looks broken; a demo needs a table. But padding
it with invented claims would be the single most dishonest thing this product
could do.

The third option: seed the **golden-case suite** — the 23 test bundles that gate
every commit. Their verdicts are computed by the same frozen engine that produces
real ones, from inputs that are openly synthetic. Together they exercise all
eight epistemic levels and both paths to a contradicted verdict, so the register
demonstrates the full ladder.

Every row is stamped `provenance: golden` in the verdict lineage, and the API
returns that as an enum the UI renders as a badge at chip size. A synthetic row
must be impossible to mistake for a measurement in a screenshot forwarded without
context.

`_provenance()` in the claims API defaults to `golden` when it cannot establish a
real source — the safe direction, since a synthetic row mislabelled as measured
is a far worse failure than the reverse.

    DATABASE_URL=... uv run python scripts/seed_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "golden"))

from app.db.session import session_scope  # noqa: E402
from app.db.verdicts import save_verdict  # noqa: E402
from app.services.reconcile import reconcile  # noqa: E402

CASES = REPO_ROOT / "tests" / "golden" / "cases"

# One synthetic hierarchy for every golden claim. Reuses the demo district so the
# register's partition routing is exercised, but a distinct project code so a
# golden row can never be confused with the measured one.
DDL = """
INSERT INTO watersheds (ws_code, geom)
VALUES ('GOLDEN-WS', ST_GeomFromText(:poly, 4326))
ON CONFLICT (ws_code) DO NOTHING;

INSERT INTO sub_watersheds (sws_code, watershed_id, geom)
SELECT 'GOLDEN-SWS', id, ST_GeomFromText(:poly, 4326)
FROM watersheds WHERE ws_code = 'GOLDEN-WS'
ON CONFLICT (sws_code) DO NOTHING;

INSERT INTO micro_watersheds
    (mws_code, sub_ws_id, state_lgd, district_lgd, geom, analysis_srid)
SELECT 'GOLDEN-MWS', id, '27', '520', ST_GeomFromText(:poly, 4326), 32643
FROM sub_watersheds WHERE sws_code = 'GOLDEN-SWS'
ON CONFLICT (mws_code) DO NOTHING;

INSERT INTO projects (project_code, name, mws_id, state_lgd, district_lgd)
SELECT 'GOLDEN-SUITE', 'Golden case suite (synthetic)', id, '27', '520'
FROM micro_watersheds WHERE mws_code = 'GOLDEN-MWS'
ON CONFLICT (project_code) DO NOTHING;
"""

INTERVENTION = """
INSERT INTO interventions (unique_id, project_id, mws_id, district_lgd, type,
    status, completed_date, geom, expected_footprint_m2)
SELECT :unique_id, p.id, m.id, '520', CAST(:itype AS intervention_type),
    'completed', :claim_date, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :footprint
FROM projects p, micro_watersheds m
WHERE p.project_code = 'GOLDEN-SUITE' AND m.mws_code = 'GOLDEN-MWS'
ON CONFLICT (unique_id) DO NOTHING
"""

CLAIM = """
INSERT INTO claims (intervention_id, district_lgd, asserted_status,
    asserted_date, geom, uncertainty_m, detectability)
SELECT id, '520', 'completed', :claim_date,
    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :acc,
    :detect
FROM interventions WHERE unique_id = :unique_id
RETURNING id
"""

CLAIM_LOOKUP = """
SELECT c.id FROM claims c JOIN interventions i ON i.id = c.intervention_id
WHERE i.unique_id = :unique_id ORDER BY c.id LIMIT 1
"""

POLY = "MULTIPOLYGON(((76.9 18.9, 77.0 18.9, 77.0 19.0, 76.9 19.0, 76.9 18.9)))"


def load_case(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def main() -> int:
    from test_golden import build_bundle  # the suite's own loader

    paths = sorted(CASES.glob("*.yaml"))
    if not paths:
        print(f"no golden cases in {CASES}", file=sys.stderr)
        return 2

    seeded = 0
    with session_scope() as session:
        for stmt in filter(None, (s.strip() for s in DDL.split(";"))):
            session.execute(text(stmt), {"poly": POLY})

        for i, path in enumerate(paths):
            spec = load_case(path)
            bundle = build_bundle(spec)
            verdict = reconcile(bundle)

            # The case files already name themselves GOLD-NN-DESCRIPTION. Adding a
            # GOLDEN- prefix produced GOLDEN-GOLD-01-... ; the provenance badge
            # carries that information, so the id does not need to.
            unique_id = bundle.claim_id[:32]
            # Spread the synthetic points on a line so the register's map inset
            # and the partition key both get exercised without overlapping pins.
            lon, lat = 76.92 + (i % 12) * 0.004, 18.92 + (i // 12) * 0.004
            params = {
                "unique_id": unique_id,
                "itype": bundle.intervention_type,
                "claim_date": "2023-11-20",
                "lon": lon,
                "lat": lat,
                "footprint": float(bundle.gates.expected_footprint_m2),
                "acc": 8.0,
                "detect": "passed" if bundle.gates.detectability_passed else "failed",
            }
            session.execute(text(INTERVENTION), params)
            existing = session.execute(text(CLAIM_LOOKUP), {"unique_id": unique_id}).scalar()
            claim_id = (
                int(existing)
                if existing is not None
                else int(session.execute(text(CLAIM), params).scalar_one())
            )

            save_verdict(
                session,
                verdict,
                bundle,
                claim_id=claim_id,
                extra_lineage={
                    # Read by the API's `_provenance()`. Explicit, because the
                    # default must not be relied on to classify these.
                    "provenance": f"GOLDEN CASE — synthetic bundle from {path.name}",
                    "golden_case": path.name,
                },
            )
            seeded += 1
            print(f"  {path.name:<48} {verdict.level.value:<24} {verdict.label}")

    print(f"\nseeded {seeded} golden cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
