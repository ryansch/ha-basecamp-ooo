"""Tests for the basecamp_ooo integration setup."""

from collections.abc import Generator
from datetime import date
import time
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.basecamp_ooo.const import (
    CONF_ACCOUNT_ID,
    CONF_BUCKET_ID,
    CONF_PERSON_ID,
    CONF_RUN_TIME,
    CONF_SCHEDULE_ID,
    DOMAIN,
    OAUTH_TOKEN,
)
from custom_components.basecamp_ooo.runner import BasecampOooRunner
from custom_components.basecamp_ooo.stretches import OutOfOffice, Result

ACCOUNT_ID = 9990001
BUCKET_ID = 9990002
SCHEDULE_ID = 9990003
PERSON_ID = 9990004

ENTRY = {
    "id": 101,
    "all_day": True,
    "starts_at": "2026-11-13",
    "ends_at": "2026-11-16",
    "participants": [{"id": PERSON_ID}],
}
OOO_OFF = OutOfOffice(enabled=False, start=None, end=None)
OOO_SET = OutOfOffice(enabled=True, start=date(2026, 11, 13), end=date(2026, 11, 16))


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
    """The mocked BasecampClient instance."""
    client = MagicMock()
    client.list_schedule_entries = AsyncMock(return_value=[ENTRY])
    client.get_out_of_office = AsyncMock(return_value=OOO_OFF)
    client.set_out_of_office = AsyncMock(
        side_effect=lambda person_id, start, end: OutOfOffice(True, start, end)
    )
    return client


@pytest.fixture
def client_cls(client: MagicMock) -> Generator[MagicMock]:
    """Patch BasecampClient in __init__; the class mock returns `client`."""
    with patch(
        "custom_components.basecamp_ooo.BasecampClient", return_value=client
    ) as cls:
        yield cls


def _entry(
    hass: HomeAssistant, options: dict | None = None, **token
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Corp",
        unique_id=f"{ACCOUNT_ID}-{PERSON_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 1209600,
                "expires_at": time.time() + 1209600,
                **token,
            },
            CONF_ACCOUNT_ID: ACCOUNT_ID,
            CONF_BUCKET_ID: BUCKET_ID,
            CONF_SCHEDULE_ID: SCHEDULE_ID,
            CONF_PERSON_ID: PERSON_ID,
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _fire_at_local(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, local: str
) -> None:
    now = dt_util.parse_datetime(f"2026-11-13T{local}-04:00")
    freezer.move_to(now)
    async_fire_time_changed(hass, now)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_manifest_loads(hass: HomeAssistant) -> None:
    """The manifest declares a config flow and depends on application_credentials."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == "basecamp_ooo"
    assert integration.config_flow is True
    assert "application_credentials" in integration.dependencies


async def test_setup_and_unload(
    hass: HomeAssistant, client_cls: MagicMock, client: MagicMock
) -> None:
    """Setup creates the runner as runtime_data with a working client; unload works."""
    entry = _entry(hass)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, BasecampOooRunner)
    _session, token_getter, account_id = client_cls.call_args.args
    assert account_id == ACCOUNT_ID
    assert await token_getter() == "access-token"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_runs_once_after_ha_started(
    hass: HomeAssistant, client_cls: MagicMock, client: MagicMock
) -> None:
    """While HA is starting, the first run waits for EVENT_HOMEASSISTANT_STARTED."""
    hass.set_state(CoreState.not_running)
    entry = _entry(hass)

    await _setup(hass, entry)
    client.list_schedule_entries.assert_not_awaited()

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.list_schedule_entries.await_count == 1
    assert entry.runtime_data.data.result is Result.SET
    client.set_out_of_office.assert_awaited_once_with(
        PERSON_ID, date(2026, 11, 13), date(2026, 11, 16)
    )


async def test_setup_runs_immediately_when_ha_running(
    hass: HomeAssistant, client_cls: MagicMock, client: MagicMock
) -> None:
    """If HA is already running (integration added or reloaded), run once now."""
    entry = _entry(hass)

    await _setup(hass, entry)

    assert client.list_schedule_entries.await_count == 1
    assert entry.runtime_data.data.result is Result.SET


async def test_startup_catch_up_after_morning_run_does_not_post(
    hass: HomeAssistant, client_cls: MagicMock, client: MagicMock
) -> None:
    """A restart after the morning run finds OOO active and does not POST."""
    client.get_out_of_office.return_value = OOO_SET
    entry = _entry(hass)

    await _setup(hass, entry)

    client.set_out_of_office.assert_not_awaited()
    assert entry.runtime_data.data.result is Result.ALREADY_ACTIVE


async def test_scheduled_at_run_time_option(
    hass: HomeAssistant, client_cls: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """The daily run fires at the configured local run time, not the default."""
    entry = _entry(hass, options={CONF_RUN_TIME: "07:15:00"})
    with patch.object(BasecampOooRunner, "run_now", AsyncMock()) as run_now:
        await _setup(hass, entry)
        assert run_now.await_count == 1  # startup run

        await _fire_at_local(hass, freezer, "06:00:00")
        await _fire_at_local(hass, freezer, "07:14:59")
        assert run_now.await_count == 1

        await _fire_at_local(hass, freezer, "07:15:00")
        assert run_now.await_count == 2


async def test_scheduled_at_default_run_time(
    hass: HomeAssistant, client_cls: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """Without the option the daily run fires at 06:00:00 local."""
    entry = _entry(hass)
    with patch.object(BasecampOooRunner, "run_now", AsyncMock()) as run_now:
        await _setup(hass, entry)

        await _fire_at_local(hass, freezer, "06:00:00")
        assert run_now.await_count == 2


async def test_options_change_reloads_entry(
    hass: HomeAssistant, client_cls: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """Changing the run time reloads the entry and reschedules the run."""
    entry = _entry(hass)
    with patch.object(BasecampOooRunner, "run_now", AsyncMock()) as run_now:
        await _setup(hass, entry)
        first_runner = entry.runtime_data

        hass.config_entries.async_update_entry(
            entry, options={CONF_RUN_TIME: "07:15:00"}
        )
        await hass.async_block_till_done(wait_background_tasks=True)

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not first_runner
        calls = run_now.await_count

        await _fire_at_local(hass, freezer, "06:00:00")
        assert run_now.await_count == calls  # old schedule is gone

        await _fire_at_local(hass, freezer, "07:15:00")
        assert run_now.await_count == calls + 1


async def test_token_update_does_not_reload_entry(
    hass: HomeAssistant, client_cls: MagicMock
) -> None:
    """A refreshed token written to entry.data must not reload the entry."""
    entry = _entry(hass)
    await _setup(hass, entry)
    runner = entry.runtime_data

    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "token": {**entry.data["token"], "access_token": "x"}},
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data is runner


async def test_token_refresh_failure_starts_reauth(
    hass: HomeAssistant,
    client_cls: MagicMock,
    client: MagicMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected refresh token (Launchpad 401) records `error` and starts reauth."""
    aioclient_mock.post(OAUTH_TOKEN, status=401, json={"error": "invalid_grant"})
    token_getter = None

    def capture(session, getter, account_id):
        nonlocal token_getter
        token_getter = getter
        return client

    async def list_entries(schedule_id: int) -> list[dict]:
        await token_getter()  # the real client fetches a token before each request
        return [ENTRY]

    client_cls.side_effect = capture
    client.list_schedule_entries.side_effect = list_entries
    entry = _entry(hass, expires_at=0)  # expired: token_getter must refresh

    await _setup(hass, entry)

    assert entry.runtime_data.data.result is Result.ERROR
    assert list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    client.set_out_of_office.assert_not_awaited()
