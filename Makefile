.PHONY: setup develop lint lint-check test help setup-go2rtc

# Default target
help:
	@echo "Available targets:"
	@echo "  setup	    - Install dependencies from requirements.txt"
	@echo "  develop	- Start Home Assistant"
	@echo "  develop-debug - Start Home Assistant with remote debugger (debugpy)"
	@echo "  lint	    - Format and fix code with ruff"
	@echo "  lint-check - Check code without fixing (for CI)"
	@echo "  test	    - Run unit tests with pytest"
	@echo "  test-cov   - Run tests with coverage"
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
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" hass --config config --debug

# Start Home Assistant in remote debug mode (debugpy)
# Listens on port 5678 and waits for debugger to attach
debug:
	@if [ ! -d "config" ]; then \
		mkdir -p config; \
		hass --config config --script ensure_config; \
	fi
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
test:
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" pytest tests/ -v

# Run tests with coverage
test-cov:
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" pytest tests/ -v --cov=custom_components --cov-report=term-missing
