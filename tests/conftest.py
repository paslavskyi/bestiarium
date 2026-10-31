"""Test configuration for bestiarium_mode."""

import logging
from collections.abc import Iterator

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

HA_USAGE_REPORT_LOGGERS = (
    "homeassistant.helpers.frame",
    "homeassistant.helpers.deprecation",
)


class _DeprecatedEntityMembers(logging.Handler):
    """
    Collect HA's reports of deprecated members in Bestiarium entity classes.

    HA 2026.11 reports them once, when the class is defined, so this handler is
    installed before any Bestiarium module is imported.
    """

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.reports: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "bestiarium_" in record.name and "provides the deprecated" in message:
            self.reports.append(message)


_deprecated_entity_members = _DeprecatedEntityMembers()
logging.getLogger().addHandler(_deprecated_entity_members)


@pytest.fixture
def deprecated_entity_members() -> list[str]:
    """Return what HA reported as deprecated in a Bestiarium entity class."""
    return _deprecated_entity_members.reports


@pytest.fixture(autouse=True)
def fail_on_deprecated_ha_usage(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail the test if HA reports deprecated API usage by a Bestiarium integration."""
    yield
    reports = [
        message
        for record in caplog.get_records("call")
        if (message := record.getMessage())
        and (
            (record.name in HA_USAGE_REPORT_LOGGERS and "bestiarium_" in message)
            # HA 2026.11 reports a deprecated entity member through its module
            or ("bestiarium_" in record.name and "deprecated" in message)
        )
    ]
    if reports:
        pytest.fail("Deprecated Home Assistant API usage:\n" + "\n".join(reports))
