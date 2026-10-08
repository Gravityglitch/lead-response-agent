.DEFAULT_GOAL := help
.PHONY: help install lint fmt test run eval migrate up down logs build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

lint: ## Run ruff lint and format checks
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and auto-fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run tests with coverage gate (80%)
	uv run pytest -q --cov=agent --cov-report=term-missing --cov-fail-under=80

run: ## Run the API locally with reload
	uv run uvicorn agent.api:app --reload

eval: ## Run the evals
	uv run python -m agent.eval

migrate: ## Apply database migrations
	uv run alembic upgrade head

up: ## Build and start the stack in Docker
	docker compose up --build -d

down: ## Stop the Docker stack
	docker compose down

logs: ## Follow API container logs
	docker compose logs -f api

build: ## Build the Docker image
	docker build -t lead-response-agent .
