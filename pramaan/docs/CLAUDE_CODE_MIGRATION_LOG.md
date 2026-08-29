# PRAMAAN — Session Migration Log

**Target Agent:** Claude Code (or any subsequent LLM agent)
**Date:** 2026-08-29
**Project:** Smart India Hackathon 2026, Problem Statement 26015
**Thesis:** Turn geo-tagged field photographs into verifiable claims by reconciling them against independent satellite, terrain, and temporal evidence. The ceiling is L4 (control-differenced); the system explicitly refuses to make causal claims.

---

## 1. Current State of the Codebase

The core analytical pipeline, database, API, and frontend console are **built, tested, and integrated**. We have successfully run a real claim through the engine using real NASA HLS imagery and NASADEM terrain derivatives.

### Backend (Python 3.11, FastAPI, Celery, PostgreSQL/PostGIS)
*   **Engine (`app/services/reconcile`)**: Pure function. Fully built, frozen as `engine-v1`. 100% branch coverage.
*   **Evidence Producers**: All 6 family adapters are built and strictly typed (`terrain`, `satellite`, `temporal`, `control`, `context`, `photo`).
*   **Persistence (`app/db`)**: Raw SQL (`text()`) via SQLAlchemy Core. **No ORM models are used** to prevent drift from the Alembic DDL.
*   **Database (`db/migrations`)**: 2 migrations. Tables: `claims`, `interventions`, `evidence` (LIST partitioned), `verdicts`, `watersheds`, etc.
*   **M8 Integration (`app/workers/reconcile.py`)**: Celery task `reconcile_claim` consumes a JSON wire payload, runs the engine, and persists the bundle and 6 evidence rows in one transaction.
*   **API (`app/api/v1`)**:
    *   `GET /claims` (Register)
    *   `GET /claims/{id}/evidence` (Evidence Tree)
    *   `GET /claims/{id}/temporal` (Temporal/Control chart data projection)
    *   `GET /claims/{id}/verdict`
    *   `POST /verdicts/{id}/recompute` (Audit proof, verifies digest hash)
    *   `GET /method/*` (Prints live engine configuration)

### Frontend (React, TypeScript, Vite)
*   **Aesthetic**: "Survey Record". Serif body (`Source Serif 4`), Mono numbers/IDs (`JetBrains Mono`). No generic UI sans-serif. Minimal floating cards; uses hairlines.
*   **Screens Built**:
    *   **S0**: Shell/Rail
    *   **S1**: Claims Register (showing all 8 epistemic levels, golden vs. measured badges).
    *   **S2**: Reconciliation Detail (Level before confidence, Evidence Tree, un-collapsible Dissent panel, to-scale Uncertainty Disk).
    *   **S3/S7**: Temporal Analysis (Hand-rolled SVG chart, 1 line per season, shaded control ribbon, hatched construction band).
    *   **S4**: Method Drawer (reads live from API).

### Data & Scripts
*   `scripts/build_temporal_series.py`: Fetches HLS from CMR STAC, builds windowed reads, extracts NDVI/MNDWI. (Measured: 10 season-windows).
*   `scripts/build_terrain.py`: Fetches NASADEM, runs WhiteboxTools hydrology chain (breach, flow accum, strahler, slope, distance to stream), samples covariates over an uncertainty disk, and selects matched controls.
*   `scripts/seed_demo.py`: Seeds the real measured data into the database.
*   `scripts/seed_golden.py`: Seeds 23 synthetic golden test cases.

---

## 2. Strict Architectural Rules (DO NOT BREAK THESE)

1.  **No ORM Models:** Do not create SQLAlchemy `Base` models. The schema lives in `db/migrations/versions`. Use `sqlalchemy.text()`.
2.  **No Single-Pixel Sampling:** Terrain covariates MUST be sampled over an uncertainty disk (`DiskStat` min/median/max). A single pixel gives false precision.
3.  **Unavailable != Neutral:** A missing evidence family (or cloud-covered scene) lowers *coverage* (e_a = 0) but does NOT score as neutral agreement 0.0.
4.  **Level before Confidence:** Confidence is mathematically bounded by the score (`confidence <= abs(score) + 0.0005`). Do not show confidence as a standalone probability.
5.  **Colors encode semantics ONLY:** Green (`--l4`, `--l3`, `--l2`) means mathematically corroborated. Amber (`--n1`) means inconclusive. Rust (`--n3`) means contradicted. Do not use green for "success" UI elements or buttons.
6.  **Minimal Frontend Dependencies:** The chart is raw SVG. Do not install charting libraries (like Chart.js or Recharts) or animation libraries (like Framer Motion/GSAP).
7.  **The API reads, it does not recompute:** `/claims/{id}/temporal` reads from `evidence.lineage`. It does not recalculate deltas, so the chart cannot disagree with the stored verdict.

---

## 3. The Immediate Next Steps (Critical Path)

Pick up exactly where this session left off:

*   **Adjudication Ledger (S9 / Backend)**: The UI buttons (Accept/Edit/Reject) on the Detail screen are currently disabled. You need to build the `POST /claims/{id}/adjudicate` endpoint. This must write to an append-only, hash-chained `adjudications` table (the schema exists).
*   **M1 (District Data)**: Bulk imagery acquisition pipeline for a full district.
*   **M3/M6 (Photo AI)**: Field collection schema (`ml/annotation/schema.py` is frozen) and standing up the SigLIP-2 zero-shot pipeline for the `photo` family.
*   **M4 (Ingestion)**: Building `app.workers.ingestion` to process EXIF metadata from uploaded images.
*   **M7 (API Security)**: JWT RBAC implementation.

---

## 4. Runbook for the New Agent

To stand up the environment and verify tests:

```bash
# 1. Start the stack and seed the databases (Real HLS/Terrain + Golden cases)
make demo-up

# 2. Start the frontend
make web

# 3. Run the full test suite (458 tests, 100% branch coverage)
make check
```

**Context Files to Read First:**
*   `docs/PRAMAAN_SIH26015_Master_Research_and_Design.md` (The master spec)
*   `docs/13-terrain.md` and `docs/14-ui-design-system.md`
*   `Makefile` and `pramaan/RUNNING.md`
*   `app/api/v1/verdicts.py` and `app/workers/reconcile.py`
