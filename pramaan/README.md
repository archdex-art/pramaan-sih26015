# PRAMAAN

**Photo-Referenced Analytics for Monitoring of Assets And Natural-resources**

Smart India Hackathon 2026 · Problem Statement 26015 · Department of Land Resources

---

PRAMAAN turns every geo-tagged watershed photograph into a machine-testable
claim, reconciles that claim against the satellite and terrain record, and
returns a verdict with calibrated confidence, an explicit epistemic level, a
dissent panel and a full lineage record — for a human officer to adjudicate.

It is an intelligence layer over DoLR's existing SRISHTI/DRISHTI geotag
pipeline. It does not replace Bhuvan. It answers the question Bhuvan does not:
*"does the satellite record agree with the photograph, and did the structure
change anything?"*

## Quickstart

```bash
cp .env.example .env          # host ports; defaults avoid common collisions
docker compose up -d          # postgres+postgis · redis · minio · titiler · api · worker
curl localhost:8000/healthz
open http://localhost:8000/api/v1/docs
```

On Apple silicon, add the arm64 overlay for a natively-built Postgres:

```bash
docker compose -f docker-compose.yml -f docker-compose.arm64.yml up -d
```

## The engine

`backend/app/services/reconcile/` is the product. Everything else is a producer
that feeds it or a surface that displays it.

```python
from app.services.reconcile import reconcile
verdict = reconcile(bundle)     # pure function: EvidenceBundle -> Verdict
```

It is a **pure function** — no IO, no network, no clock, no randomness, no
global state. Given the same evidence bundle it returns a byte-identical verdict
forever. That property is what makes machine output usable as government
evidence, and it is enforced mechanically, not by convention:

| Guarantee | Enforced by |
|---|---|
| No IO, no clock, no RNG anywhere in the engine | `tests/unit/test_engine_purity.py` walks the package AST |
| `confidence ≤ \|score\|` | dataclass `__post_init__`, a Hypothesis property, **and** a Postgres `CHECK` constraint |
| Every verdict carries a dissent panel | `Verdict.__post_init__` refuses to construct one without it |
| Weights sum to exactly 1.00 | `_validate_weights()` at import — the engine will not start otherwise |
| The design doc's published numbers are reproducible | `scripts/render_worked_examples.py --check` in CI |
| The system never emits accusatory language | `scripts/vocabulary_lint.py` in CI |
| All 8 epistemic levels remain reachable | asserted by the golden suite itself |

Current state: **84 tests, 100 % branch coverage on the engine, `mypy --strict` clean.**

## Commands

```bash
make check              # everything CI runs, offline. Run before pushing.
make golden             # the GT-3 golden-case suite (the demo's insurance policy)
make purity             # prove the engine is still a pure function
make examples           # regenerate the design doc's worked examples from the engine
make vocab              # vocabulary lock
make verify-endpoints   # re-test every external data source, rewrite docs/09
make migrate            # alembic upgrade head
```

## What the system refuses to do

This list is a feature, and it is published rather than buried:

- **It never issues a causal verdict.** The ceiling is L4 (control-differenced).
  `L5_causal` is absent from the engine's `Level` enum entirely — not merely
  unreachable — so no code path can construct it.
- **It never treats absence of evidence as evidence of absence.** A structure
  smaller than one 900 m² pixel cannot be contradicted by "we looked and saw
  nothing"; the detectability gate blocks that path before any satellite
  evidence is computed.
- **It never concludes anything, positive or negative, below the data-sufficiency
  floor.** A cloud-blocked season yields `N1 INCONCLUSIVE`, not a success and not
  a problem.
- **It never accuses.** The strongest phrase available anywhere in the UI, API or
  PDF is *"requires physical verification"*, enforced in CI.
- **It never reports a single composite "watershed health score".** An indicator
  panel with per-indicator uncertainty, by design.
- **It never lets AI become government evidence on its own.** Every verdict is
  PROVISIONAL until a named officer accepts, edits or rejects it, and the
  decision is written to an append-only, hash-chained ledger from which
  `UPDATE` and `DELETE` are revoked at the database role level.

Types with no optical signature — dug well, borewell, recharge shaft, livestock,
livelihood — are capped at existence-only and say so in every verdict. See
`GET /api/v1/method/signatures`.

## Repository layout

```
backend/app/services/reconcile/   ★ the engine: pure, no IO, 100% covered
backend/app/api/v1/method.py        the engine explaining itself to the UI
db/migrations/                      alembic; baseline verified against real PostGIS
tests/golden/cases/*.yaml           GT-3: 22 declarative cases, all 8 levels
tests/unit/test_engine_purity.py    AST proof that the engine stays pure
scripts/verify_endpoints.py       ★ turns "we verified the APIs" into an artefact
scripts/render_worked_examples.py   makes the design doc's numbers reproducible
scripts/vocabulary_lint.py          the vocabulary lock
docs/09-data-sources.md             generated: real request log, failures included
```

## Data sources

`docs/09-data-sources.md` is **generated** by `scripts/verify_endpoints.py` and
records the result of an actual request to every external dependency, including
the ones that failed and the ones we have no credentials for. It is regenerated
weekly in CI so it is never stale at judging time.

Measured findings worth knowing before you build against them:

- Bhuvan's WMS `GetCapabilities` is **~6.7 MB / ~4,600 layers and takes ~110 s**.
  It must be cached at district onboarding, never fetched per request. Of the
  four documented hosts only `bhuvan-vec2` answers this path.
- The Copernicus Data Space Sentinel-2 collection id is `sentinel-2-l2a`
  (lowercase). The obvious guess, `SENTINEL-2`, returns HTTP 400.
- NRSC Bhoonidhi requires a government credential we do not hold as a
  non-departmental team. Reported as `SKIPPED_NO_CREDENTIALS`, which is a stated
  constraint, not an oversight.

## Licence

Apache-2.0. See `LICENSE`.
