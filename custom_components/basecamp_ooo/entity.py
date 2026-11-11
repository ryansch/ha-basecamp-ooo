"""Shared base entity for Basecamp Out of Office."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .runner import BasecampOooRunner

DEVICE_NAME = "Basecamp OOO"


class BasecampOooEntity(CoordinatorEntity[BasecampOooRunner]):
    """An entity backed by the runner, on the integration's service device."""

    _attr_has_entity_name = True

    def __init__(self, runner: BasecampOooRunner, key: str) -> None:
        """Initialize the entity; `key` is its translation key and ID suffix."""
        super().__init__(runner)
        unique_id = runner.config_entry.unique_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(unique_id))},
            name=DEVICE_NAME,
            manufacturer="37signals",
            model="Basecamp",
            entry_type=DeviceEntryType.SERVICE,
        )
