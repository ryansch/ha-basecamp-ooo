"""The Basecamp Out of Office integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import BasecampClient
from .const import CONF_ACCOUNT_ID, CONF_RUN_TIME, DEFAULT_RUN_TIME, DOMAIN
from .runner import STORAGE_VERSION, BasecampOooRunner, storage_key

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

type BasecampOooConfigEntry = ConfigEntry[BasecampOooRunner]


def _run_time_option(entry: ConfigEntry) -> str:
    return entry.options.get(CONF_RUN_TIME, DEFAULT_RUN_TIME)


async def async_setup_entry(hass: HomeAssistant, entry: BasecampOooConfigEntry) -> bool:
    """Set up Basecamp Out of Office from a config entry."""
    loaded_run_time = _run_time_option(entry)
    run_time = dt_util.parse_time(loaded_run_time)
    if run_time is None:
        raise ValueError(f"Invalid run time {loaded_run_time!r}")

    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def token_getter() -> str:
        await session.async_ensure_token_valid()
        return session.token["access_token"]

    client = BasecampClient(
        async_get_clientsession(hass), token_getter, entry.data[CONF_ACCOUNT_ID]
    )
    runner = BasecampOooRunner(hass, entry, client)
    entry.runtime_data = runner

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _start_run(*_: object) -> None:
        # Background task: cancelled on unload, never blocks startup.
        entry.async_create_background_task(
            hass, runner.run_now(), f"{DOMAIN} run {entry.entry_id}"
        )

    @callback
    def _scheduled(_now: datetime) -> None:
        _start_run()

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _scheduled,
            hour=run_time.hour,
            minute=run_time.minute,
            second=run_time.second,
        )
    )
    # Catch-up run once HA has started (immediately if it already has).
    entry.async_on_unload(async_at_started(hass, _start_run))

    async def _async_entry_updated(
        hass: HomeAssistant, entry: BasecampOooConfigEntry
    ) -> None:
        # Token refreshes also update the entry; reload only for option changes.
        if _run_time_option(entry) != loaded_run_time:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BasecampOooConfigEntry
) -> bool:
    """Unload a Basecamp Out of Office config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: BasecampOooConfigEntry
) -> None:
    """Delete the entry's persisted run state when the entry is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
