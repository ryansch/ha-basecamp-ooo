"""Tests for the basecamp_ooo sensors, binary sensor, and button."""

from collections.abc import Generator
from dataclasses import replace
from datetime import date, timedelta
import time
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.basecamp_ooo.const import (
    CONF_ACCOUNT_ID,
    CONF_BUCKET_ID,
    CONF_PERSON_ID,
    CONF_SCHEDULE_ID,
    DOMAIN,
)
from custom_components.basecamp_ooo.stretches import OutOfOffice

ACCOUNT_ID = 9990001
BUCKET_ID = 9990002
SCHEDULE_ID = 9990003
PERSON_ID = 9990004
UNIQUE_ID = f"{ACCOUNT_ID}-{PERSON_ID}"

# Two entries bridged across the weekend: Fri 2026-11-13 .. Mon 2026-11-16.
ENTRIES = [
    {
        "id": 101,
        "all_day": True,
        "starts_at": "2026-11-13",
        "ends_at": "2026-11-13",
        "participants": [{"id": PERSON_ID}],
    },
    {
        "id": 102,
        "all_day": True,
        "starts_at": "2026-11-16",
        "ends_at": "2026-11-16",
        "participants": [{"id": PERSON_ID}],
    },
]
OOO_OFF = OutOfOffice(enabled=False, start=None, end=None)

NEXT_TIME_OFF = "sensor.basecamp_out_of_office_next_time_off"
LAST_RUN = "sensor.basecamp_out_of_office_last_run"
ACTIVE = "binary_sensor.basecamp_out_of_office_active"
RUN_NOW = "button.basecamp_out_of_office_run_now"


@pytest.fixture(autouse=True)
async def halifax(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Use the user's time zone; 05:00 local on the first day off."""
    await hass.config.async_set_time_zone("America/Halifax")
    freezer.move_to("2026-11-13 09:00:00+00:00")


@pytest.fixture(autouse=True)
async def setup_credentials(hass: HomeAssistant) -> None:
    """Register application credentials for the integration."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential("client-id", "client-secret"), DOMAIN
    )


@pytest.fixture
def client() -> MagicMock:
    """The mocked BasecampClient: one stretch, OOO off, POST echoes the dates."""
    client = MagicMock()
    client.list_schedule_entries = AsyncMock(return_value=ENTRIES)
    client.get_out_of_office = AsyncMock(return_value=OOO_OFF)
    client.set_out_of_office = AsyncMock(
        side_effect=lambda person_id, start, end: OutOfOffice(True, start, end)
    )
    return client


@pytest.fixture(autouse=True)
def client_cls(client: MagicMock) -> Generator[MagicMock]:
    """Patch BasecampClient in __init__; the class mock returns `client`."""
    with patch(
        "custom_components.basecamp_ooo.BasecampClient", return_value=client
    ) as cls:
        yield cls


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry added to hass (not set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Corp",
        unique_id=UNIQUE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 1209600,
                "expires_at": time.time() + 1209600,
            },
            CONF_ACCOUNT_ID: ACCOUNT_ID,
            CONF_BUCKET_ID: BUCKET_ID,
            CONF_SCHEDULE_ID: SCHEDULE_ID,
            CONF_PERSON_ID: PERSON_ID,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_next_time_off_state_and_attributes(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The next-time-off sensor shows the stretch start, end and entry IDs."""
    await _setup(hass, entry)  # HA is running: the startup run happens now

    state = hass.states.get(NEXT_TIME_OFF)
    assert state is not None
    assert state.state == "2026-11-13"
    assert state.attributes[ATTR_DEVICE_CLASS] == "date"
    assert state.attributes["end"] == "2026-11-16"
    assert state.attributes["entry_ids"] == [101, 102]

    entity = er.async_get(hass).async_get(NEXT_TIME_OFF)
    assert entity.unique_id == f"{UNIQUE_ID}_next_time_off"
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.name == "Basecamp Out of Office"


async def test_next_time_off_unknown_when_none(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> None:
    """Before the first run every entity renders unknown/off cleanly."""
    hass.set_state(CoreState.not_running)  # startup run waits for HA started

    await _setup(hass, entry)

    client.list_schedule_entries.assert_not_awaited()
    next_time_off = hass.states.get(NEXT_TIME_OFF)
    assert next_time_off.state == STATE_UNKNOWN
    assert "end" not in next_time_off.attributes
    assert "entry_ids" not in next_time_off.attributes
    last_run = hass.states.get(LAST_RUN)
    assert last_run.state == STATE_UNKNOWN
    assert last_run.attributes["result"] is None
    assert last_run.attributes["set_start"] is None
    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert hass.states.get(RUN_NOW).state == STATE_UNKNOWN


async def test_last_run_attributes_after_set(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """After a run that sets OOO, last_run carries the timestamp and outcome."""
    await _setup(hass, entry)

    state = hass.states.get(LAST_RUN)
    assert state.attributes[ATTR_DEVICE_CLASS] == "timestamp"
    assert dt_util.parse_datetime(state.state) == dt_util.parse_datetime(
        "2026-11-13T09:00:00+00:00"
    )
    assert state.attributes["result"] == "set"
    assert state.attributes["message"] == "Set out-of-office 2026-11-13 to 2026-11-16."
    assert state.attributes["set_start"] == "2026-11-13"
    assert state.attributes["set_end"] == "2026-11-16"

    entity = er.async_get(hass).async_get(LAST_RUN)
    assert entity.unique_id == f"{UNIQUE_ID}_last_run"


async def test_active_binary_sensor_on_and_off(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Active is on only while an enabled OOO covers today (HA local date)."""
    await _setup(hass, entry)
    runner = entry.runtime_data
    assert hass.states.get(ACTIVE).state == STATE_ON  # the run just set it

    # Enabled but entirely in the future: off.
    runner.async_set_updated_data(
        replace(
            runner.data,
            ooo=OutOfOffice(True, date(2026, 11, 14), date(2026, 11, 16)),
        )
    )
    assert hass.states.get(ACTIVE).state == STATE_OFF

    # Ended yesterday (local), even though it is 2026-11-13 in UTC already: off.
    runner.async_set_updated_data(
        replace(
            runner.data,
            ooo=OutOfOffice(True, date(2026, 11, 10), date(2026, 11, 12)),
        )
    )
    assert hass.states.get(ACTIVE).state == STATE_OFF

    # Single day, today: on.
    runner.async_set_updated_data(
        replace(
            runner.data,
            ooo=OutOfOffice(True, date(2026, 11, 13), date(2026, 11, 13)),
        )
    )
    assert hass.states.get(ACTIVE).state == STATE_ON

    # Disabled: off.
    runner.async_set_updated_data(replace(runner.data, ooo=OOO_OFF))
    assert hass.states.get(ACTIVE).state == STATE_OFF

    entity = er.async_get(hass).async_get(ACTIVE)
    assert entity.unique_id == f"{UNIQUE_ID}_active"


async def test_run_now_button_triggers_run(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> None:
    """Pressing Run now performs a full run and updates the entities."""
    hass.set_state(CoreState.not_running)
    await _setup(hass, entry)
    client.list_schedule_entries.assert_not_awaited()

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: RUN_NOW}, blocking=True
    )
    await hass.async_block_till_done()

    assert client.list_schedule_entries.await_count == 1
    client.set_out_of_office.assert_awaited_once_with(
        PERSON_ID, date(2026, 11, 13), date(2026, 11, 16)
    )
    assert hass.states.get(LAST_RUN).attributes["result"] == "set"
    assert hass.states.get(ACTIVE).state == STATE_ON

    entity = er.async_get(hass).async_get(RUN_NOW)
    assert entity.unique_id == f"{UNIQUE_ID}_run_now"


async def test_last_run_shows_error_after_failed_run(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> None:
    """A failed run is visible on last_run as result `error` with a message."""
    hass.set_state(CoreState.not_running)
    client.list_schedule_entries.side_effect = RuntimeError("Basecamp is down")
    await _setup(hass, entry)

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: RUN_NOW}, blocking=True
    )
    await hass.async_block_till_done()

    state = hass.states.get(LAST_RUN)
    assert state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert state.attributes["result"] == "error"
    assert "Basecamp is down" in state.attributes["message"]


async def test_last_run_stays_available_when_poll_fails(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing 30-minute OOO poll must not hide the last run's error."""
    client.list_schedule_entries.side_effect = RuntimeError("Basecamp is down")
    await _setup(hass, entry)  # startup run errors
    assert hass.states.get(LAST_RUN).attributes["result"] == "error"

    client.get_out_of_office.side_effect = RuntimeError("still down")
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data.last_update_success is False  # the poll failed
    state = hass.states.get(LAST_RUN)
    assert state.state != STATE_UNAVAILABLE
    assert state.attributes["result"] == "error"
    assert "Basecamp is down" in state.attributes["message"]
