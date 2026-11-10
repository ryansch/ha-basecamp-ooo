"""Binary sensor for Basecamp Out of Office."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BasecampOooConfigEntry
from .entity import BasecampOooEntity
from .runner import BasecampOooRunner

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BasecampOooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor."""
    async_add_entities([OutOfOfficeActiveBinarySensor(entry.runtime_data)])


class OutOfOfficeActiveBinarySensor(BasecampOooEntity, BinarySensorEntity):
    """On while Basecamp out-of-office is enabled and covers today."""

    def __init__(self, runner: BasecampOooRunner) -> None:
        """Initialize the binary sensor."""
        super().__init__(runner, "active")

    @property
    def is_on(self) -> bool:
        """Return True if out-of-office is enabled for today."""
        ooo = self.coordinator.data.ooo
        if ooo is None or not ooo.enabled or ooo.start is None or ooo.end is None:
            return False
        return ooo.start <= dt_util.now().date() <= ooo.end
