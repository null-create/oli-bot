# Convenience commands for local development and testing of the agent
PHONY: init clean test run build restart rebuild stop

init:
	@echo "Initializing backend..."
	@python3 -m venv venv
	@./venv/bin/pip install --upgrade pip
	@./venv/bin/pip install -e .

clean:
	@echo "Cleaning up..."
	@rm -rf ./venv

test:
	@echo "Running tests..."
	@pytest -v tests/

run:
	@echo "Running agent in container with API server..."
	@docker compose up -d

stop:
	@echo "Stopping Agent..."
	@docker compose down

restart: 
	@echo "Restarting Agent..."
	@make stop
	@make run

build:
	@echo "Building and running agent..."
	@docker compose -d -build

rebuild:
	@echo "Rebuilding Agent..."
	@make stop
	@make build
