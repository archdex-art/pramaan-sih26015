# Running PRAMAAN

Verified from a cold start on 2026-08-29. Every command below was run, not
assumed.

## Prerequisites

| Need | Check |
|---|---|
| Docker Desktop, running | `docker ps` |
| `uv` | `uv --version` |
| Node 20+ | `node --version` |

Nothing else. No API keys, no accounts, no network beyond Docker Hub on the
first build.

---

## The demo — two commands

```bash
cd pramaan

make demo-up      # stack + migrations + both seeds   (~15 s warm, ~3 min cold)
make web          # the console                       (~5 s)
```

Then open **<http://127.0.0.1:5173>**.

`make demo-up` is idempotent — run it again any time to reset the data.

### What `demo-up` actually does

1. `docker compose up -d --build --remove-orphans` — postgres/postgis, redis,
   minio, titiler, api, and **two** celery workers (fast and raster, split
   because a raster job must not queue behind a photo upload)
2. Waits for Postgres to accept three consecutive real queries — `pg_isready`
   goes true during the image's own init pass, before the server restarts for
   real connections, and that race cost a false "migration failed" once
3. The `migrate` service applies both migrations; the API will not start
   against an unmigrated schema
4. `make seed` — the **measured** claim, from real HLS + NASADEM data
5. `make seed-golden` — the 23 golden cases, badged `golden`

Expect **24 claims · 1 measured · 8/8 epistemic levels**.

---

## Verify it worked

```bash
curl -s localhost:8000/healthz
# {"status":"ok","engine_version":"engine-v1","offline_mode":"false"}

curl -s localhost:8000/api/v1/claims | python3 -c \
  'import json,sys;r=json.load(sys.stdin);print(len(r),"claims,",
   sum(1 for x in r if x["provenance"]=="measured"),"measured")'
# 24 claims, 1 measured
```

The audit proof, in one request:

```bash
curl -s -X POST localhost:8000/api/v1/verdicts/1/recompute | python3 -m json.tool
# "identical": true   — the engine reproduced the verdict from its stored lineage
```

---

## The demo path through the console

| Step | Where | Say |
|---|---|---|
| 1 | Register | "24 claims. All eight epistemic levels. One row is `MEASURED` — that's the only one from real imagery. The other 23 are the test bundles that gate every commit, and they're badged so you can't mistake them." |
| 2 | Click the `MEASURED` row | "Level first, then confidence. N1 inconclusive, confidence 0.06." |
| 3 | Point at the disk | "That's the GPS uncertainty disk drawn to scale against the 30 m pixel grid, with the expected footprint. We never sample a single pixel." |
| 4 | Expand `terrain` | "Strahler order 0, 277 m from any drainage line. Implausible siting — and that's arithmetic on an elevation map, no AI." |
| 5 | Point at the dissent panel | "Always shown, never collapsible. A verdict without stated counter-evidence isn't shippable." |
| 6 | **Temporal analysis** | "Site rose +0.116. Twelve terrain-matched controls rose +0.090. The site is inside the band — a naive dashboard reports success here." |
| 7 | **Method** | "Read from the running engine, not hardcoded. Change a weight and this panel changes." |

---

## Other targets

```bash
make check         # everything CI runs: lint, mypy --strict, 458 tests, 100 % coverage
make test-db       # 39 integration tests against a throwaway PostGIS, torn down after
make web-check     # frontend typecheck + production build
make down          # stop and delete volumes
```

### Targets that need credentials and network

Not needed for the demo — the measured outputs they produce are committed
(`data/demo/*.json`).

```bash
make series           # rebuild the HLS index series      (EARTHDATA_TOKEN, ~5 min)
make terrain          # rebuild DEM derivatives           (needs .hgt tiles, ~4 s)
make measure-windows  # windowed-read cost measurement    (EARTHDATA_TOKEN)
make verify-endpoints # probe the 8 government endpoints
```

`data/demo/dem/*.hgt` is gitignored — 223 MB and re-downloadable. `make terrain`
will tell you to fetch them if they are missing.

---

## Ports

Published in the 5xxxx range on purpose: Postgres 5432, Redis 6379 and MinIO
9000/9001 collide with almost every other Docker project a developer has
running. Override any of them in `.env`.

| Service | Host | Override |
|---|---|---|
| API | 8000 | `PRAMAAN_API_PORT` |
| Postgres | **55432** | `PRAMAAN_PG_PORT` |
| Redis | 56379 | `PRAMAAN_REDIS_PORT` |
| MinIO | 59000 / 59001 | `PRAMAAN_MINIO_PORT` |
| TiTiler | 58001 | `PRAMAAN_TITILER_PORT` |
| Console (dev) | 5173 | — |

---

## If something breaks

**Port already in use.** Something else holds it. Find it and either stop it or
override the port:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

**`make seed` fails with a connection error.** Postgres is not up yet, or is on a
different port. Check `docker compose ps` and `PRAMAAN_PG_PORT`.

**Console loads but every panel says it cannot reach the API.** The Vite dev
server proxies `/api` and `/healthz` to `127.0.0.1:8000`. Confirm the API is up:
`curl -s localhost:8000/healthz`.

**Console shows stale rows after re-seeding.** It should not — API reads are
`no-store`. If it happens, hard-reload.

**Everything is confusing.** Start clean:

```bash
make down && make demo-up
```

### Seeding runs on the host, deliberately

`make seed` runs on **your machine** against the containerised Postgres, not
inside the `api` container. `backend/Dockerfile` copies only `app/`, so there is
no `scripts/` in the image, and the repo-relative data paths the seed scripts use
would not resolve there either.

An earlier version of this Makefile had
`docker compose exec api python scripts/seed_demo.py`. It could never have
worked. Found by running it while writing this file.

---

## Offline

The demo needs no network once the images are built and the seeds have run:

- fonts are self-hosted (`frontend/public/fonts`, 264 KB) — a webfont CDN call is
  exactly what becomes a fallback-to-Times failure on stage
- measured data is committed as JSON
- no external call is made on any page load

To prove it, disable the network interface and run `make demo-up && make web`.
`docker-compose.demo.yml` additionally sets `PRAMAAN_OFFLINE=1` and puts the
services on an internal network with no egress.
