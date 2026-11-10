.PHONY: up down test lint format status verify backfill
up:
	docker compose up --build -d
down:
	docker compose down
test:
	docker compose run --rm --no-deps tests
lint:
	docker compose run --rm --no-deps --entrypoint /opt/pipeline-venv/bin/ruff pipeline check etl ml dags tests docker scripts
format:
	docker compose run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$$(pwd):/opt/project" --entrypoint /opt/pipeline-venv/bin/ruff pipeline format etl ml dags tests docker scripts
status:
	docker compose run --rm --no-deps pipeline status
verify:
	docker compose run --rm --no-deps --entrypoint /opt/pipeline-venv/bin/python pipeline scripts/verify_stack.py --replay --services
backfill:
	docker compose run --rm --no-deps pipeline run --start-date $(START) --end-date $(END)
