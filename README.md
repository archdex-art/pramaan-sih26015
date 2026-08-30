# PRAMAAN — प्रमाण

**Photo-Referenced Analytics for Monitoring of Assets And Natural-resources**

Smart India Hackathon 2026 · Problem Statement **26015** · Department of Land
Resources, Ministry of Rural Development
*Application of Geospatial Techniques for Visualization and Analysis to
Interpret Geo-Coded Images to Enhance Watershed Development Outcomes*

---

PRAMAAN turns every geo-tagged watershed photograph into a **machine-testable
claim**, reconciles that claim against the independent satellite and terrain
record, and returns a verdict carrying a calibrated confidence, an explicit
epistemic level, a dissent panel and a full lineage record — for a named human
officer to adjudicate.

`प्रमाण` is Sanskrit for *proof*.

## What is in this repository

| Path | Contents |
|---|---|
| [`pramaan/`](pramaan/) | The system: FastAPI backend, React console, PostGIS schema, tests. Start at [`pramaan/README.md`](pramaan/README.md). |
| [`pramaan/RUNNING.md`](pramaan/RUNNING.md) | Verified runbook. Every command in it was executed, not assumed. |
| [`docs/`](docs/) | Submission artefacts: the master research and design document (~3.7k lines), pitch deck, architecture figures, speaking scripts. |

## Run it

```bash
cd pramaan
make demo-up     # stack + migrations + seeds  (~15 s warm, ~3 min cold)
make web         # console on http://127.0.0.1:5173
```

No API keys and no accounts are required. The demo overlay runs with the
network cable out.

## The three workspaces

One deployment, three role-scoped consoles. The role → workspace mapping lives
server-side in `app/core/authz.py`; the interface reads it rather than hardcoding
role names. All accounts below use the password `pramaan-demo-2026`.

| Login | Workspace | What it opens on | Can it sign? |
|---|---|---|---|
| `wdt.nanded`, `pia.nanded` | Field | Submissions · Record evidence | No — files claims only |
| `wcdc.nanded`, `slna.mh` | Monitoring | Claims register · Verifications · Analytics | **Yes** — signs the ledger |
| `audit.cag` | Monitoring (read-only) | Register · Ledger · Audit trail | No |
| `admin.dolr` | Administration | Users · Districts · Data sources | No — separation of duties |

## What it actually does

- **Six evidence families**, five of them independent of the claim: terrain
  (DEM hydrology), satellite (30 m indices), temporal (seasonal trend), matched
  controls, rainfall context — and photo, deliberately weighted **lowest**,
  because a photograph is the claim's own source.
- **An eight-level epistemic ladder** (`L0`–`L4`, `N1`–`N3`) with a hard ceiling
  at `L4 control-differenced`. There is no causal level, and none can be
  constructed.
- **A reconciliation engine that is a pure function** — bundle in, verdict out,
  no IO, no clock, no randomness — pinned by 23 golden cases covering all eight
  levels and by property tests, at 100 % branch coverage.
- **Real evidence capture**: EXIF/GPS extraction, a classical-CV quality gate
  (variance-of-Laplacian blur, histogram exposure — no ML), DCT perceptual-hash
  deduplication, MIME sniffing and full re-encode before storage.
- **An append-only, hash-chained adjudication ledger.** The database revokes
  `UPDATE` and `DELETE` from the application role, and the chain is verifiable
  from `psql` without this application running:
  `python scripts/verify_ledger_chain.py`.
- **Byte-identical reproducibility**: `POST /api/v1/verdicts/{id}/recompute`
  re-derives a stored verdict from its own lineage and returns
  `identical: true`.

## What it does not claim

This matters more than the feature list.

- **No accuracy figure.** No labelled ground-truth corpus of Indian watershed
  photographs exists — we checked ten sources and recorded the results in
  [`pramaan/docs/09-data-sources.md`](pramaan/docs/09-data-sources.md). A number
  here would measure self-consistency and be read as correctness.
- **No causal claim.** Vegetation rising near a check dam does not prove the
  check dam caused it. The ladder stops below causation and every report says so.
- **Absence of evidence is not evidence of absence.** A structure below the 30 m
  detection limit has its per-structure satellite claim *disabled* and is
  escalated to cluster assessment. This is a hard gate, not a guideline.
- **Nothing is evidence until a named officer signs it.** Every unadjudicated
  verdict is labelled `PROVISIONAL` everywhere, including in exported reports.

The one claim in the demo that runs on real NASA HLS imagery comes out
**`N1 inconclusive`, confidence 0.0615** — the site's vegetation genuinely rose
`+0.116`, twelve terrain-matched control sites rose `+0.090`, and the difference
is inside the noise. A dashboard would have reported success. This reports what
the data supports.

## Verification

```bash
cd pramaan
make check      # lint · mypy --strict · 488 tests · 100 % branch coverage
make test-db    # 620 tests against a throwaway PostGIS, torn down after
```

Both were green at the last commit. `make check` runs fully offline and never
depends on a government endpoint being up.

## Licence

See [`pramaan/LICENSE`](pramaan/LICENSE).

---

**Prototype — not a deployed system.** Photo model inference and the bulk
district imagery pipeline are not built; `docs/` states precisely which parts are
built and tested, which are not built, and why.
