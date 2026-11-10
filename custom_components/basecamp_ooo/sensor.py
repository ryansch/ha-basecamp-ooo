"""Sensors for Basecamp Out of Office."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BasecampOooConfigEntry
from .entity import BasecampOooEntity
from .runner import BasecampOooRunner

PARALLEL_UPDATES = 0


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BasecampOooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    runner = entry.runtime_data
    async_add_entities([NextTimeOffSensor(runner), LastRunSensor(runner)])


class NextTimeOffSensor(BasecampOooEntity, SensorEntity):
    """The start of the current or next time-off stretch."""

    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, runner: BasecampOooRunner) -> None:
        """Initialize the sensor."""
        super().__init__(runner, "next_time_off")

    @property
    def native_value(self) -> date | None:
        """Return the stretch start date."""
        stretch = self.coordinator.data.next_stretch
        return stretch.start if stretch else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the stretch end date and the entries that make it up."""
        stretch = self.coordinator.data.next_stretch
        if stretch is None:
            return None
        return {"end": stretch.end.isoformat(), "entry_ids": list(stretch.entry_ids)}


class LastRunSensor(BasecampOooEntity, SensorEntity):
    """When the daily run last ran, and what it did."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runner: BasecampOooRunner) -> None:
        """Initialize the sensor."""
        super().__init__(runner, "last_run")

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last run."""
        return self.coordinator.data.last_run

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the outcome of the last run."""
        data = self.coordinator.data
        return {
            "result": str(data.result) if data.result is not None else None,
            "message": data.message or None,
            "set_start": _iso(data.set_start),
            "set_end": _iso(data.set_end),
        }
