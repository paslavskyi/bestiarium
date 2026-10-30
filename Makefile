.PHONY: setup develop lint lint-check test test-card help setup-go2rtc stand

# Default target
help:
	@echo "Available targets:"
	@echo "  setup	    - Install dependencies from requirements.txt"
	@echo "  develop	- Start Home Assistant"
	@echo "  develop-debug - Start Home Assistant with remote debugger (debugpy)"
	@echo "  lint	    - Format and fix code with ruff"
	@echo "  lint-check - Check code without fixing (for CI)"
	@echo "  test	    - Run unit tests with pytest and the card tests"
	@echo "  test-card  - Run the tests of the power flow card with node"
	@echo "  test-cov   - Run tests with coverage"
	@echo "  stand      - Set up the test stand in the running development HA"
	@echo "  help	    - Show this help message"
# Install dependencies
setup:
	python3 -m pip install --requirement requirements.txt

# Start Home Assistant development server
develop:
	@if [ ! -d "config" ]; then \
		mkdir -p config; \
		hass --config config --script ensure_config; \
	fi
	touch config/automations.yaml config/scripts.yaml
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" hass --config config --debug

# Start Home Assistant in remote debug mode (debugpy)
# Listens on port 5678 and waits for debugger to attach
debug:
	@if [ ! -d "config" ]; then \
		mkdir -p config; \
		hass --config config --script ensure_config; \
	fi
	touch config/automations.yaml config/scripts.yaml
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" python -Xfrozen_modules=off -m debugpy --listen 5678 --wait-for-client -m homeassistant --config config --debug

# Format and fix code with ruff
lint:
	ruff format .
	ruff check . --fix

# Check code without fixing (for CI)
lint-check:
	ruff format --check .
	ruff check .

# Run unit tests
test: test-card
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" pytest tests/ -v

# Run the tests of the power flow card (plain JS, no packages)
test-card:
	node --test "tests/frontend/*.test.mjs"

# Run tests with coverage
test-cov:
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" pytest tests/ -v --cov=custom_components --cov-report=term-missing

# Set up the test stand (Bestiarium Mock sockets, their Bestiarium Devices and
# Climate entries, scenario scripts, dashboard) in the HA of `make develop`.
# Needs a long-lived access token of that HA in HA_TEST_ENV_TOKEN.
stand:
	PYTHONPATH="$(CURDIR)" python3 scripts/stand_setup.py
