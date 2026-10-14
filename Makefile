.PHONY: sync db-up migrate doctor check db-stop
sync:
	uv sync --frozen
db-up:
	docker compose up -d --wait
migrate:
	uv run python scripts/with-local-env.py alembic upgrade head
doctor:
	uv run python scripts/with-local-env.py aegisops doctor
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run lint-imports
	uv run python scripts/with-local-env.py pytest
	uv run pip-audit
	gitleaks git --redact --no-banner
db-stop:
	docker compose stop
