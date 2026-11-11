"""Tests for the basecamp_ooo daily runner."""

from collections.abc import AsyncGenerator
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientResponseError, RequestInfo
from freezegun.api import FrozenDateTimeFactory
import pytest
from yarl import URL

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.basecamp_ooo.api import BasecampApiError, BasecampAuthError
from custom_components.basecamp_ooo.const import (
    CONF_ACCOUNT_ID,
    CONF_BUCKET_ID,
    CONF_PERSON_ID,
    CONF_SCHEDULE_ID,
    DOMAIN,
)
from custom_components.basecamp_ooo.runner import (
    ISSUE_RUN_FAILED,
    BasecampOooRunner,
    RunState,
)
from custom_components.basecamp_ooo.stretches import OutOfOffice, Result, Stretch

ACCOUNT_ID = 9990001
BUCKET_ID = 9990002
SCHEDULE_ID = 9990003
PERSON_ID = 9990004

# Friday 2026-11-13 through Monday 2026-11-16 (one entry, weekend inside).
ENTRY = {
    "id": 101,
    "all_day": True,
    "starts_at": "2026-11-13",
    "ends_at": "2026-11-16",
    "participants": [{"id": PERSON_ID}],
}
STRETCH = Stretch(date(2026, 11, 13), date(2026, 11, 16), (101,))
OOO_OFF = OutOfOffice(enabled=False, start=None, end=None)
OOO_SET = OutOfOffice(enabled=True, start=date(2026, 11, 13), end=date(2026, 11, 16))

# 06:00 America/Halifax (AST, UTC-4) on the first day of the stretch.
FIRST_DAY_MORNING = "2026-11-13 10:00:00+00:00"


def _reauth_flows(hass: HomeAssistant, entry: MockConfigEntry) -> list:
    return list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


def _client_response_error(status: int) -> ClientResponseError:
    info = RequestInfo(
        URL("https://example.com"), "POST", {}, URL("https://example.com")
    )
    return ClientResponseError(info, (), status=status)


@pytest.fixture(autouse=True)
async def halifax(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Use the user's time zone and freeze at 06:00 local on the first day off."""
    await hass.config.async_set_time_zone("America/Halifax")
    freezer.move_to(FIRST_DAY_MORNING)


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry added to hass (not set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Corp",
        unique_id=f"{ACCOUNT_ID}-{PERSON_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {"access_token": "a", "refresh_token": "r", "expires_at": 0},
            CONF_ACCOUNT_ID: ACCOUNT_ID,
            CONF_BUCKET_ID: BUCKET_ID,
            CONF_SCHEDULE_ID: SCHEDULE_ID,
            CONF_PERSON_ID: PERSON_ID,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client() -> MagicMock:
    """A mocked BasecampClient: one stretch, OOO off, POST echoes the dates."""
    client = MagicMock()
    client.list_schedule_entries = AsyncMock(return_value=[ENTRY])
    client.get_out_of_office = AsyncMock(return_value=OOO_OFF)
    client.set_out_of_office = AsyncMock(
        side_effect=lambda person_id, start, end: OutOfOffice(True, start, end)
    )
    return client


@pytest.fixture
async def runner(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> AsyncGenerator[BasecampOooRunner]:
    """A runner wired to the mocked client (pending retries cancelled after)."""
    runner = BasecampOooRunner(hass, entry, client)
    yield runner
    runner.async_cancel_retry()


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float
) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def test_initial_state_is_empty(runner: BasecampOooRunner) -> None:
    """Before any run the runner holds an empty RunState."""
    assert runner.data == RunState(
        last_run=None,
        result=None,
        message="",
        set_start=None,
        set_end=None,
        next_stretch=None,
        ooo=None,
    )


async def test_run_sets_ooo_on_first_day(
    hass: HomeAssistant, runner: BasecampOooRunner, client: MagicMock
) -> None:
    """On the first day of a stretch the run sets OOO for the whole stretch."""
    state = await runner.run_now()

    client.list_schedule_entries.assert_awaited_once_with(SCHEDULE_ID)
    client.set_out_of_office.assert_awaited_once_with(
        PERSON_ID, date(2026, 11, 13), date(2026, 11, 16)
    )
    assert state.result is Result.SET
    assert state.set_start == date(2026, 11, 13)
    assert state.set_end == date(2026, 11, 16)
    assert state.ooo == OOO_SET  # the OOO returned by the POST
    assert state.next_stretch == STRETCH
    assert state.last_run is not None
    assert state.message
    assert runner.data is state


async def test_run_twice_same_morning_posts_once(
    runner: BasecampOooRunner, client: MagicMock
) -> None:
    """A second run re-reads OOO, sees it active, and does not POST again."""
    client.get_out_of_office.side_effect = [OOO_OFF, OOO_SET]

    first = await runner.run_now()
    second = await runner.run_now()

    assert first.result is Result.SET
    assert second.result is Result.ALREADY_ACTIVE
    assert client.set_out_of_office.await_count == 1
    assert client.get_out_of_office.await_count == 2
    assert second.set_start is None
    assert second.ooo == OOO_SET


async def test_mid_stretch_does_not_post(
    runner: BasecampOooRunner, client: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """Later days of a stretch never POST."""
    freezer.move_to("2026-11-16 10:00:00+00:00")

    state = await runner.run_now()

    assert state.result is Result.MID_STRETCH
    client.set_out_of_office.assert_not_awaited()


async def test_existing_future_ooo_not_replaced(
    runner: BasecampOooRunner, client: MagicMock
) -> None:
    """An enabled OOO that does not cover today is left alone."""
    future = OutOfOffice(True, date(2026, 12, 1), date(2026, 12, 5))
    client.get_out_of_office.return_value = future

    state = await runner.run_now()

    assert state.result is Result.SKIPPED_EXISTING_FUTURE
    assert state.ooo == future
    client.set_out_of_office.assert_not_awaited()


async def test_uses_ha_local_date_near_midnight(
    runner: BasecampOooRunner, client: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """11-13 03:30 UTC is still 11-12 in Halifax, so the stretch has not begun."""
    freezer.move_to("2026-11-13 03:30:00+00:00")

    state = await runner.run_now()

    assert state.result is Result.NO_TIME_OFF_TODAY
    assert state.next_stretch == STRETCH
    client.set_out_of_office.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("list_schedule_entries", BasecampApiError("HTTP 500")),
        ("get_out_of_office", ValueError("Invalid isoformat string: 'garbage'")),
        ("set_out_of_office", RuntimeError("unexpected")),
        (
            "get_out_of_office",
            OAuth2TokenRequestTransientError(
                request_info=_client_response_error(503).request_info,
                history=(),
                status=503,
                domain=DOMAIN,
            ),
        ),
    ],
    ids=["api_error", "malformed_ooo", "unexpected", "token_refresh_transient"],
)
async def test_api_error_records_error_and_creates_repair(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    entry: MockConfigEntry,
    method: str,
    error: Exception,
) -> None:
    """Any failure records `error` and raises the run_failed repair; never escapes."""
    getattr(client, method).side_effect = error

    state = await runner.run_now()

    assert state.result is Result.ERROR
    assert str(error) in state.message
    assert runner.data is state
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RUN_FAILED)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert _reauth_flows(hass, entry) == []


async def test_error_keeps_previous_ooo_and_next_stretch(
    runner: BasecampOooRunner, client: MagicMock
) -> None:
    """A failed run keeps the last known OOO and stretch for display."""
    await runner.run_now()
    client.list_schedule_entries.side_effect = BasecampApiError("down")

    state = await runner.run_now()

    assert state.result is Result.ERROR
    assert state.ooo == OOO_SET
    assert state.next_stretch == STRETCH
    assert state.set_start is None


async def test_success_after_error_clears_repair(
    hass: HomeAssistant, runner: BasecampOooRunner, client: MagicMock
) -> None:
    """The next non-error run deletes the run_failed repair."""
    client.list_schedule_entries.side_effect = BasecampApiError("down")
    await runner.run_now()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RUN_FAILED) is not None

    client.list_schedule_entries.side_effect = None
    state = await runner.run_now()

    assert state.result is Result.SET
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RUN_FAILED) is None


async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    entry: MockConfigEntry,
) -> None:
    """A 401 from Basecamp records `error` and starts a reauth flow."""
    client.list_schedule_entries.side_effect = BasecampAuthError("unauthorized")

    state = await runner.run_now()
    await hass.async_block_till_done()

    assert state.result is Result.ERROR
    assert len(_reauth_flows(hass, entry)) == 1
    client.set_out_of_office.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        OAuth2TokenRequestReauthError(
            request_info=_client_response_error(400).request_info,
            history=(),
            status=400,
            domain=DOMAIN,
        ),
        _client_response_error(400),
        _client_response_error(401),
    ],
    ids=["reauth_error", "client_400", "client_401"],
)
async def test_token_refresh_reauth_errors_start_reauth(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    entry: MockConfigEntry,
    error: Exception,
) -> None:
    """Token refresh errors that need a new login start reauth (via token_getter)."""
    client.list_schedule_entries.side_effect = error

    state = await runner.run_now()
    await hass.async_block_till_done()

    assert state.result is Result.ERROR
    assert len(_reauth_flows(hass, entry)) == 1


async def test_periodic_update_refreshes_only_ooo(
    runner: BasecampOooRunner, client: MagicMock
) -> None:
    """The 30-minute refresh re-reads OOO and keeps the last run's result."""
    await runner.run_now()
    client.get_out_of_office.reset_mock()
    client.list_schedule_entries.reset_mock()
    later = OutOfOffice(True, date(2026, 11, 13), date(2026, 11, 20))
    client.get_out_of_office.return_value = later

    await runner.async_refresh()

    assert runner.last_update_success
    assert runner.data.ooo == later
    assert runner.data.result is Result.SET
    assert runner.data.set_start == date(2026, 11, 13)
    client.list_schedule_entries.assert_not_awaited()
    client.set_out_of_office.assert_awaited_once()  # only the run's POST


@pytest.mark.parametrize(
    "error",
    [
        BasecampAuthError("unauthorized"),
        OAuth2TokenRequestReauthError(
            request_info=_client_response_error(400).request_info,
            history=(),
            status=400,
            domain=DOMAIN,
        ),
        _client_response_error(401),
    ],
    ids=["basecamp_401", "token_reauth", "client_401"],
)
async def test_periodic_update_auth_failure_raises_auth_failed(
    runner: BasecampOooRunner, client: MagicMock, error: Exception
) -> None:
    """Auth failures in the periodic refresh raise ConfigEntryAuthFailed."""
    client.get_out_of_office.side_effect = error

    with pytest.raises(ConfigEntryAuthFailed):
        await runner._async_update_data()


@pytest.mark.parametrize(
    "error",
    [
        BasecampApiError("HTTP 500"),
        ValueError("bad date"),
        OAuth2TokenRequestTransientError(
            request_info=_client_response_error(503).request_info,
            history=(),
            status=503,
            domain=DOMAIN,
        ),
    ],
    ids=["api_error", "malformed_ooo", "token_transient"],
)
async def test_periodic_update_other_failure_raises_update_failed(
    runner: BasecampOooRunner, client: MagicMock, error: Exception
) -> None:
    """Other failures in the periodic refresh raise UpdateFailed."""
    client.get_out_of_office.side_effect = error

    with pytest.raises(UpdateFailed):
        await runner._async_update_data()


def test_update_interval_is_30_minutes(runner: BasecampOooRunner) -> None:
    """The periodic OOO refresh runs every 30 minutes."""
    assert runner.update_interval.total_seconds() == 30 * 60


@pytest.mark.parametrize(
    "returned",
    [
        OutOfOffice(True, date(2026, 11, 13), date(2026, 11, 14)),
        OutOfOffice(True, date(2026, 11, 12), date(2026, 11, 16)),
        OutOfOffice(False, None, None),
    ],
    ids=["wrong_end", "wrong_start", "not_enabled"],
)
async def test_post_response_mismatch_records_error(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    returned: OutOfOffice,
) -> None:
    """A POST whose response doesn't match the stretch is an error, not a set."""
    client.set_out_of_office.side_effect = None
    client.set_out_of_office.return_value = returned

    state = await runner.run_now()

    assert state.result is Result.ERROR
    assert "2026-11-13 to 2026-11-16" in state.message  # expected
    assert f"{returned.start} to {returned.end}" in state.message  # returned
    assert state.set_start is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RUN_FAILED) is not None
    assert _store_key(entry) not in hass_storage  # not recorded as set


ALREADY_SET_MESSAGE = (
    "Out-of-office was already set for this stretch today; not setting it again."
)


def _store_key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}"


async def test_set_records_stretch_start_in_store(
    runner: BasecampOooRunner, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """A successful set persists the stretch start it set."""
    await runner.run_now()

    stored = hass_storage[_store_key(entry)]
    assert stored["version"] == 1
    assert stored["data"] == {"set_start": "2026-11-13"}


async def test_cleared_ooo_not_reset_after_restart(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    entry: MockConfigEntry,
) -> None:
    """OOO cleared by hand after the set stays cleared across a restart/reload."""
    first = await runner.run_now()
    assert first.result is Result.SET

    # Restart: a fresh runner for the same entry; the user cleared the OOO.
    client.get_out_of_office.return_value = OOO_OFF
    restarted = BasecampOooRunner(hass, entry, client)
    state = await restarted.run_now()

    assert client.set_out_of_office.await_count == 1
    assert state.result is Result.ALREADY_ACTIVE
    assert state.message == ALREADY_SET_MESSAGE
    assert state.set_start is None
    assert state.set_end is None
    assert state.ooo == OOO_OFF


async def test_cleared_ooo_not_reset_by_second_run_same_session(
    runner: BasecampOooRunner, client: MagicMock
) -> None:
    """Run now after clearing the OOO by hand does not set it again."""
    await runner.run_now()
    client.get_out_of_office.return_value = OOO_OFF

    state = await runner.run_now()

    assert client.set_out_of_office.await_count == 1
    assert state.result is Result.ALREADY_ACTIVE
    assert state.message == ALREADY_SET_MESSAGE


async def test_stored_start_of_other_stretch_does_not_block(
    hass: HomeAssistant,
    client: MagicMock,
    entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """A stored start from an earlier stretch does not stop this one being set."""
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "key": _store_key(entry),
        "data": {"set_start": "2026-11-02"},
    }
    runner = BasecampOooRunner(hass, entry, client)

    state = await runner.run_now()

    assert state.result is Result.SET
    client.set_out_of_office.assert_awaited_once()
    assert hass_storage[_store_key(entry)]["data"] == {"set_start": "2026-11-13"}


async def test_error_run_retries_same_day(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed run is retried 5 minutes later and the retry sets OOO."""
    client.list_schedule_entries.side_effect = [BasecampApiError("down"), [ENTRY]]

    assert (await runner.run_now()).result is Result.ERROR

    await _advance(hass, freezer, 4)
    assert client.list_schedule_entries.await_count == 1

    await _advance(hass, freezer, 1)
    assert client.list_schedule_entries.await_count == 2
    assert runner.data.result is Result.SET
    client.set_out_of_office.assert_awaited_once()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RUN_FAILED) is None

    await _advance(hass, freezer, 120)  # success ends the retries
    assert client.list_schedule_entries.await_count == 2


async def test_retry_backoff_5_15_then_every_30_minutes(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Retries back off 5, 15, then 30 minutes while runs keep failing."""
    client.list_schedule_entries.side_effect = BasecampApiError("down")
    await runner.run_now()

    expected = 1
    for delay in (5, 15, 30, 30, 30):
        await _advance(hass, freezer, delay - 1)
        assert client.list_schedule_entries.await_count == expected
        await _advance(hass, freezer, 1)
        expected += 1
        assert client.list_schedule_entries.await_count == expected


async def test_retry_stops_at_local_midnight(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A retry due after local midnight does not run (03:50 UTC is 23:50 AST)."""
    freezer.move_to("2026-11-13 03:50:00+00:00")
    client.list_schedule_entries.side_effect = BasecampApiError("down")
    await runner.run_now()

    await _advance(hass, freezer, 5)  # 23:55 local: same day, retried
    assert client.list_schedule_entries.await_count == 2

    await _advance(hass, freezer, 15)  # 00:10 local on a new day: not run
    await _advance(hass, freezer, 120)
    assert client.list_schedule_entries.await_count == 2


async def test_auth_error_not_retried(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Auth failures go to reauth and are not retried."""
    client.list_schedule_entries.side_effect = BasecampAuthError("unauthorized")
    await runner.run_now()

    await _advance(hass, freezer, 120)

    assert client.list_schedule_entries.await_count == 1


async def test_auth_error_cancels_pending_retry(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An auth failure after a retryable error stops the retries."""
    client.list_schedule_entries.side_effect = [
        BasecampApiError("down"),
        BasecampAuthError("unauthorized"),
    ]
    await runner.run_now()
    await runner.run_now()

    await _advance(hass, freezer, 120)

    assert client.list_schedule_entries.await_count == 2


async def test_successful_run_cancels_pending_retry(
    hass: HomeAssistant,
    runner: BasecampOooRunner,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A successful Run now before the retry fires cancels the retry."""
    client.list_schedule_entries.side_effect = [BasecampApiError("down"), [ENTRY]]
    await runner.run_now()
    await runner.run_now()

    await _advance(hass, freezer, 120)

    assert client.list_schedule_entries.await_count == 2
