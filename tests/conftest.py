"""Test configuration for bestiarium_mode."""

from collections.abc import Iterator

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

HA_USAGE_REPORT_LOGGERS = (
    "homeassistant.helpers.frame",
    "homeassistant.helpers.deprecation",
)


@pytest.fixture(autouse=True)
def fail_on_deprecated_ha_usage(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail the test if HA reports deprecated API usage by a Bestiarium integration."""
    yield
    reports = [
        record.getMessage()
        for record in caplog.get_records("call")
        if record.name in HA_USAGE_REPORT_LOGGERS
        and "bestiarium_" in record.getMessage()
    ]
    if reports:
        pytest.fail("Deprecated Home Assistant API usage:\n" + "\n".join(reports))
