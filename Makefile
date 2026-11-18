BACKEND := uv run --project backend

.PHONY: help setup dev dev-api dev-web run demo ingest prices backup gen-api test test-api test-web lint fix build docker-setup docker-up

help: ## show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

setup: ## install everything and run the setup wizard (writes .env)
	cd backend && uv sync
	cd frontend && npm ci
	$(BACKEND) hisaab setup

dev: ## API on :8000 and web app on :5173, both reloading on change
	$(MAKE) -j2 dev-api dev-web

dev-api:
	$(BACKEND) hisaab migrate
	$(BACKEND) uvicorn --factory hisaab.main:create_app --reload --app-dir backend/src --host 127.0.0.1 --port 8000

dev-web:
	cd frontend && npm run dev

run: build ## build the web app and serve everything on :8000
	HISAAB_STATIC_DIR=frontend/dist $(BACKEND) hisaab serve

demo: ## load a fictional household to explore (remove with: hisaab demo --remove)
	$(BACKEND) hisaab demo

ingest: ## fetch new statements from your connections (run it nightly from cron)
	$(BACKEND) hisaab ingest

prices: ## fetch NAVs and closing prices, and revalue your holdings
	$(BACKEND) hisaab prices

backup: ## write a JSON backup to backups/
	mkdir -p backups
	$(BACKEND) hisaab export backups/hisaab-$$(date +%F).json

gen-api: ## regenerate the web app's API types after changing the backend
	cd frontend && npm run gen:api

build: ## build the web app
	cd frontend && npm run build

test: test-api test-web ## run all tests

test-api:
	cd backend && uv run pytest

test-web:
	cd frontend && npm test

lint: ## lint and type-check
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run typecheck

fix: ## auto-fix lint and formatting
	cd backend && uv run ruff check --fix . && uv run ruff format .

docker-setup: ## run the setup wizard inside Docker (writes data/.env)
	mkdir -p data
	docker compose run --rm app hisaab setup --env-file /app/data/.env

docker-up: ## start with Docker, only reachable from this computer
	docker compose --profile local up -d --build
