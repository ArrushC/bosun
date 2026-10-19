.PHONY: help install dev start build test test-backend test-frontend lint typecheck types fixtures clean

help:
	@echo "make install   Install backend (uv) and frontend (npm) dependencies"
	@echo "make dev       Run backend + frontend with hot reload (http://127.0.0.1:5173)"
	@echo "make start     Build the UI and serve everything from the backend (http://127.0.0.1:8787)"
	@echo "make test      Run backend and frontend tests"
	@echo "make lint      Lint and type-check backend and frontend"
	@echo "make types     Regenerate frontend API types from the backend OpenAPI schema"

install:
	cd backend && uv sync --all-extras
	cd frontend && npm install --no-audit --no-fund

dev:
	@./scripts/dev.sh

build:
	cd frontend && npm run build

start: build
	cd backend && uv run bosun

test: test-backend test-frontend

test-backend:
	cd backend && uv run pytest -q

test-frontend:
	cd frontend && npx vitest run

lint:
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests
	cd backend && basedpyright src || uv run --with basedpyright basedpyright src
	cd frontend && npx tsc -b --pretty

typecheck: lint

types:
	cd backend && uv run python ../scripts/export_openapi.py ../frontend/openapi.json
	cd frontend && npx openapi-typescript openapi.json -o src/api/schema.d.ts && rm openapi.json

fixtures:
	cd backend && PYTHONPATH=. uv run python ../scripts/export_fixtures.py ../frontend/src/test/fixtures

clean:
	rm -rf frontend/dist backend/.pytest_cache .data
