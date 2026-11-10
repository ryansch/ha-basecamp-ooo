"""The daily run: set out-of-office on the first day of a time-off stretch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import logging

from aiohttp import ClientResponseError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BasecampAuthError, BasecampClient
from .const import CONF_PERSON_ID, CONF_SCHEDULE_ID, DOMAIN
from .stretches import (
    OutOfOffice,
    Result,
    Stretch,
    build_stretches,
    current_or_next,
    days_off,
    decide,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=30)
ISSUE_RUN_FAILED = "run_failed"

# Token-endpoint statuses that mean the refresh token itself was rejected.
_REAUTH_STATUSES = (400, 401)


@dataclass
class RunState:
    """The outcome of the last run plus the latest known out-of-office."""

    last_run: datetime | None
    result: Result | None
    message: str
    set_start: date | None
    set_end: date | None
    next_stretch: Stretch | None
    ooo: OutOfOffice | None


class UnexpectedResponseError(Exception):
    """Basecamp accepted the write but reported a different out-of-office."""


def _is_auth_failure(err: Exception) -> bool:
    """Return True if the error means the user must sign in again.

    Covers Basecamp 401s and token refresh errors surfacing from the client's
    token_getter (OAuth2TokenRequestReauthError is a ClientResponseError).
    """
    if isinstance(err, (BasecampAuthError, OAuth2TokenRequestReauthError)):
        return True
    return isinstance(err, ClientResponseError) and err.status in _REAUTH_STATUSES


class BasecampOooRunner(DataUpdateCoordinator[RunState]):
    """Runs the daily out-of-office check and keeps the OOO state fresh."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: BasecampClient
    ) -> None:
        """Initialize the runner."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.person_id: int = entry.data[CONF_PERSON_ID]
        self.schedule_id: int = entry.data[CONF_SCHEDULE_ID]
        self._run_lock = asyncio.Lock()
        self.data = RunState(
            last_run=None,
            result=None,
            message="",
            set_start=None,
            set_end=None,
            next_stretch=None,
            ooo=None,
        )

    async def _async_update_data(self) -> RunState:
        """Periodic refresh: re-read only the out-of-office setting."""
        try:
            # Serialized with run_now so a stale read can't overwrite a fresh POST.
            async with self._run_lock:
                ooo = await self.client.get_out_of_office(self.person_id)
        except Exception as err:
            if _is_auth_failure(err):
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(f"Failed to read out-of-office: {err}") from err
        return replace(self.data, ooo=ooo)

    async def run_now(self) -> RunState:
        """Perform the full daily run. Never raises; failures record `error`."""
        async with self._run_lock:
            state = await self._run()
        self._update_repair(state)
        self.async_set_updated_data(state)
        return state

    async def _run(self) -> RunState:
        now = dt_util.now()
        today = now.date()
        previous = self.data
        try:
            entries = await self.client.list_schedule_entries(self.schedule_id)
            stretches = build_stretches(days_off(entries, self.person_id))
            # Re-read immediately before deciding so "already set" uses fresh state.
            ooo = await self.client.get_out_of_office(self.person_id)
            decision = decide(today, stretches, ooo)
            stretch = decision.stretch
            set_start = set_end = None
            if decision.result is Result.SET and stretch is not None:
                ooo = await self.client.set_out_of_office(
                    self.person_id, stretch.start, stretch.end
                )
                _verify_set(ooo, stretch)
                set_start, set_end = stretch.start, stretch.end
        except Exception as err:  # a run must never escape the scheduler
            if _is_auth_failure(err):
                _LOGGER.warning("Basecamp authorization failed: %s", err)
                self.config_entry.async_start_reauth(self.hass)
                message = f"Basecamp authorization failed: {err}"
            else:
                _LOGGER.exception("Basecamp out-of-office run failed")
                message = f"Run failed: {err}"
            return RunState(
                last_run=now,
                result=Result.ERROR,
                message=message,
                set_start=None,
                set_end=None,
                next_stretch=previous.next_stretch,
                ooo=previous.ooo,
            )

        return RunState(
            last_run=now,
            result=decision.result,
            message=_message(decision.result, stretch, ooo),
            set_start=set_start,
            set_end=set_end,
            next_stretch=current_or_next(stretches, today),
            ooo=ooo,
        )

    def _update_repair(self, state: RunState) -> None:
        if state.result is Result.ERROR:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_RUN_FAILED,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_RUN_FAILED,
                translation_placeholders={"message": state.message},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_RUN_FAILED)


def _verify_set(ooo: OutOfOffice, stretch: Stretch) -> None:
    """Raise unless Basecamp reports exactly the out-of-office just written."""
    if ooo.enabled and (ooo.start, ooo.end) == (stretch.start, stretch.end):
        return
    raise UnexpectedResponseError(
        f"Basecamp returned out-of-office {ooo.start} to {ooo.end} "
        f"(enabled: {ooo.enabled}); expected {stretch.start} to {stretch.end}"
    )


def _message(result: Result, stretch: Stretch | None, ooo: OutOfOffice) -> str:
    """Return a human-readable summary of a successful run."""
    match result:
        case Result.SET:
            return f"Set out-of-office {ooo.start} to {ooo.end}."
        case Result.ALREADY_ACTIVE:
            return f"Out-of-office already active ({ooo.start} to {ooo.end})."
        case Result.SKIPPED_EXISTING_FUTURE:
            return (
                f"Left existing out-of-office ({ooo.start} to {ooo.end}) in place; "
                f"time off starts today ({stretch.start if stretch else ''})."
            )
        case Result.MID_STRETCH:
            return "Time off already in progress; nothing to do."
        case _:
            return "No time off starts today."
