.PHONY: setup develop lint lint-check test help setup-go2rtc

# Default target
help:
	@echo "Available targets:"
	@echo "  setup        - Install dependencies from requirements.txt"
	@echo "  setup-go2rtc - Download and install go2rtc binary"
	@echo "  develop      - Start Home Assistant in debug mode"
	@echo "  lint         - Format and fix code with ruff"
	@echo "  lint-check   - Check code without fixing (for CI)"
	@echo "  test         - Run unit tests with pytest"
	@echo "  help         - Show this help message"

# Install dependencies
setup:
	python3 -m pip install --requirement requirements.txt

# Download and install go2rtc binary
setup-go2rtc:
	@echo "Installing go2rtc..."
	@mkdir -p config
	@ARCH=$$(uname -m); \
	if [ "$$ARCH" = "x86_64" ]; then \
		GO2RTC_ARCH="amd64"; \
	elif [ "$$ARCH" = "aarch64" ]; then \
		GO2RTC_ARCH="arm64"; \
	else \
		echo "Unsupported architecture: $$ARCH"; \
		exit 1; \
	fi; \
	echo "Detected architecture: $$GO2RTC_ARCH"; \
	curl -L -o config/go2rtc "https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_$$GO2RTC_ARCH"; \
	chmod +x config/go2rtc; \
	echo "go2rtc installed successfully at config/go2rtc"

# Start Home Assistant development server
develop:
	@if [ ! -d "config" ]; then \
		mkdir -p config; \
		hass --config config --script ensure_config; \
	fi
	PYTHONPATH="$${PYTHONPATH}:$(CURDIR)/custom_components" hass --config config --debug

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
