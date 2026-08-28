.PHONY: up down seed test golden purity lint fmt demo verify-endpoints examples vocab migrate check bench verify-datasets

up:
	docker compose up --build

down:
	docker compose down -v

seed:
	docker compose exec api python scripts/seed_demo.py

test:
	cd backend && uv run pytest ../tests/unit ../tests/integration -q

golden:
	cd backend && uv run pytest ../tests/golden -q

purity:
	cd backend && uv run pytest ../tests/unit/test_engine_purity.py -q

lint:
	cd backend && uv run ruff check app ../scripts ../tests \
		&& uv run mypy app/services/reconcile app/services/terrain app/services/photo app/services/satellite app/services/temporal --strict

fmt:
	cd backend && uv run ruff format app

verify-endpoints:
	cd backend && uv run --with httpx python ../scripts/verify_endpoints.py --write-docs

bench:
	cd backend && uv run python ../scripts/benchmark.py

verify-datasets:
	uv run --with httpx python scripts/verify_datasets.py --write-docs

examples:
	cd backend && uv run python ../scripts/render_worked_examples.py --write

vocab:
	uv run --with pyyaml python scripts/vocabulary_lint.py

migrate:
	docker compose run --rm migrate

# Everything CI runs, in CI's order. Run this before pushing.
check: lint
	cd backend && uv run pytest ../tests -q \
		--cov=app.services.reconcile --cov=app.services.terrain \
		--cov=app.services.photo --cov=app.services.satellite \
		--cov=app.services.temporal \
		--cov-branch --cov-fail-under=100
	uv run --with pyyaml python scripts/vocabulary_lint.py
	cd backend && uv run python ../scripts/render_worked_examples.py --check

# NOTE: `verify-endpoints` is deliberately NOT part of `check`. `check` must be
# runnable offline and must never fail because a government endpoint is down.
# Endpoint verification runs on its own schedule (endpoints-weekly.yml).

demo:
	docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
