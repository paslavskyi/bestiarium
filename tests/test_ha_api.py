"""Bestiarium uses no Home Assistant API that is deprecated in the tested versions."""

import pkgutil
from importlib import import_module
from pathlib import Path

CUSTOM_COMPONENTS = Path(__file__).parent.parent / "custom_components"


def test_no_deprecated_ha_entity_members(deprecated_entity_members: list[str]) -> None:
    """No entity class provides a member HA reports as deprecated when it is defined."""
    for module in pkgutil.walk_packages(
        [str(CUSTOM_COMPONENTS)], prefix="custom_components."
    ):
        import_module(module.name)
    assert deprecated_entity_members == []  # noqa: S101
