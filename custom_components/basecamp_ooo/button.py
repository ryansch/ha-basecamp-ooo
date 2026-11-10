"""Button for Basecamp Out of Office."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BasecampOooConfigEntry
from .entity import BasecampOooEntity
from .runner import BasecampOooRunner

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BasecampOooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([RunNowButton(entry.runtime_data)])


class RunNowButton(BasecampOooEntity, ButtonEntity):
    """Run the daily out-of-office check now."""

    def __init__(self, runner: BasecampOooRunner) -> None:
        """Initialize the button."""
        super().__init__(runner, "run_now")

    async def async_press(self) -> None:
        """Perform a full run (never raises; failures are recorded as `error`)."""
        await self.coordinator.run_now()
