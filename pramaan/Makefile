.PHONY: up down demo-up seed seed-golden series terrain web web-check test test-db golden purity lint fmt demo verify-endpoints examples vocab migrate check bench verify-datasets measure-windows

up:
	docker compose up --build

down:
	docker compose down -v

# Seeds the demo claim from measured HLS data. Requires
# `data/demo/temporal_series.json`, built by `make series` (needs credentials).
# Seeding runs on the HOST against the containerised Postgres, not inside the
# api container. The image copies only `app/` (see backend/Dockerfile), so
# `docker compose exec api python scripts/...` cannot work — there is no
# scripts/ in the container, and the repo-relative data paths the scripts use
# would not resolve there either. Found by trying it.
PG_URL = postgresql+psycopg://pramaan:pramaan_dev@127.0.0.1:$(or $(PRAMAAN_PG_PORT),55432)/pramaan
SEED_DEPS = --with 'psycopg[binary]' --with numpy --with sqlalchemy --with celery \
	--with pydantic-settings --with fastapi

seed:
	DATABASE_URL="$(PG_URL)" uv run $(SEED_DEPS) python scripts/seed_demo.py

# Seeds the 23 golden cases into the register as badged synthetic claims, so the
# console demonstrates all eight epistemic levels. Every row is stamped
# `provenance: golden` and rendered with a badge at chip size.
seed-golden:
	DATABASE_URL="$(PG_URL)" uv run $(SEED_DEPS) --with pyyaml --with pytest \
		python scripts/seed_golden.py

# Rebuilds the measured seasonal index series from real NASA HLS granules.
# Needs EARTHDATA_TOKEN and network; not part of `check`, same rule as
# verify-endpoints. ~4 minutes for 10 season-windows over the demo AOI.
series:
	uv run --with httpx --with rasterio --with numpy --with pyproj \
		python scripts/build_temporal_series.py

test:
	cd backend && uv run pytest ../tests/unit ../tests/integration -q

# The reproducibility guarantee (docs §21.3) through a real PostGIS instance.
# Deliberately separate from `test`: `test` must run offline with no daemon.
# Ephemeral container, migrations applied, torn down whichever way this exits.
PG_PORT ?= 55433
TEST_DSN = host=127.0.0.1 port=$(PG_PORT) user=postgres password=t dbname=t
test-db:
	@docker rm -f pramaan_testdb >/dev/null 2>&1 || true
	# --platform is explicit: postgis/postgis publishes amd64 only, so on an
	# arm64 dev machine this runs under emulation. Deliberate, not accidental —
	# same reason docker-compose.arm64.yml exists.
	docker run -d --name pramaan_testdb --platform linux/amd64 \
		-e POSTGRES_PASSWORD=t -e POSTGRES_DB=t \
		-p $(PG_PORT):5432 postgis/postgis:16-3.4-alpine >/dev/null
	# `pg_isready` goes true during the image's own init pass, before the server
	# restarts for real connections — that race cost a false "migration failed"
	# once. Require three consecutive real queries instead.
	@ok=0; for i in $$(seq 1 90); do \
		if docker exec pramaan_testdb psql -U postgres -d t -tAc 'select 1' >/dev/null 2>&1; \
		then ok=$$((ok+1)); else ok=0; fi; \
		[ $$ok -ge 3 ] && break; sleep 1; done; \
	[ $$ok -ge 3 ] || { echo "postgres never became ready"; docker rm -f pramaan_testdb; exit 1; }
	@trap 'docker rm -f pramaan_testdb >/dev/null 2>&1' EXIT; \
	cd db && export DATABASE_URL="postgresql+psycopg://postgres:t@127.0.0.1:$(PG_PORT)/t" && \
	A="uv run --with psycopg[binary] --with alembic alembic" && \
	$$A upgrade head && \
	$$A downgrade base && $$A upgrade head && \
	cd ../backend && PRAMAAN_TEST_DSN="$(TEST_DSN)" \
		uv run --with 'psycopg[binary]' pytest ../tests/integration -q

golden:
	cd backend && uv run pytest ../tests/golden -q

purity:
	cd backend && uv run pytest ../tests/unit/test_engine_purity.py -q

lint:
	cd backend && uv run ruff check app ../scripts ../tests \
		&& uv run mypy app/services/reconcile app/services/terrain app/services/photo app/services/satellite app/services/temporal app/services/context app/services/audit app/db app/workers app/api --strict

fmt:
	cd backend && uv run ruff format app

verify-endpoints:
	cd backend && uv run --with httpx python ../scripts/verify_endpoints.py --write-docs

bench:
	cd backend && uv run python ../scripts/benchmark.py

# Exact windowed-read cost per AOI scale, from the COG's own tile index.
# Needs EARTHDATA_TOKEN and network, so — like verify-endpoints — deliberately
# NOT part of `check`. Re-run when the AOI strategy or band list changes.
measure-windows:
	uv run --with httpx --with tifffile --with rasterio python scripts/measure_window_cost.py

verify-datasets:
	uv run --with httpx python scripts/verify_datasets.py --write-docs

examples:
	cd backend && uv run python ../scripts/render_worked_examples.py --write

vocab:
	uv run --with pyyaml python scripts/vocabulary_lint.py

migrate:
	docker compose run --rm migrate

# The whole demo, in order: stack, migrations, both seeds, then the console.
# Run this and nothing else.
demo-up:
	docker compose up -d --build --remove-orphans
	@echo "waiting for postgres…"
	@ok=0; for i in $$(seq 1 90); do \
		if docker compose exec -T postgres pg_isready -U pramaan -d pramaan >/dev/null 2>&1; \
		then ok=$$((ok+1)); else ok=0; fi; \
		[ $$ok -ge 3 ] && break; sleep 1; done
	$(MAKE) seed
	$(MAKE) seed-golden
	@echo ""
	@echo "API      http://127.0.0.1:8000/api/v1/docs"
	@echo "next     make web     (console on http://127.0.0.1:5173)"

# NASADEM -> hydrology -> covariates -> matched controls. Needs EARTHDATA_TOKEN
# and the .hgt tiles in data/demo/dem. ~4 s once the tiles are cached.
terrain:
	uv run --with whitebox --with rasterio --with numpy --with pyproj \
		python scripts/build_terrain.py

# Frontend dev server. Proxies /api to 127.0.0.1:8000, so run the API first.
web:
	cd frontend && npm install && npm run dev

web-check:
	cd frontend && npm ci && npx tsc -b --noEmit && npm run build

# Everything CI runs, in CI's order. Run this before pushing.
check: lint
	cd backend && uv run pytest ../tests -q \
		--cov=app.services.reconcile --cov=app.services.terrain \
		--cov=app.services.photo --cov=app.services.satellite \
		--cov=app.services.temporal --cov=app.services.context \
		--cov=app.services.audit \
		--cov-branch --cov-fail-under=100
	uv run --with pyyaml python scripts/vocabulary_lint.py
	cd backend && uv run python ../scripts/render_worked_examples.py --check

# NOTE: `verify-endpoints` is deliberately NOT part of `check`. `check` must be
# runnable offline and must never fail because a government endpoint is down.
# Endpoint verification runs on its own schedule (endpoints-weekly.yml).

demo:
	docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
