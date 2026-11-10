"""Config flow for Basecamp Out of Office."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import aiohttp
import probatio
from yarl import URL

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .api import BasecampApiError, BasecampAuthError, BasecampClient
from .const import (
    CONF_ACCOUNT_ID,
    CONF_BUCKET_ID,
    CONF_PERSON_ID,
    CONF_RUN_TIME,
    CONF_SCHEDULE_ID,
    DEFAULT_RUN_TIME,
    DOMAIN,
    LAUNCHPAD_IDENTITY,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

CONF_SCHEDULE_URL = "schedule_url"

_SCHEDULE_HOSTS = {"3.basecamp.com", "app.basecamp.com"}
_SCHEDULE_PATH = re.compile(r"^/(\d+)/buckets/(\d+)/schedules/(\d+)/?$")


def parse_schedule_url(url: str) -> tuple[int, int, int] | None:
    """Return (account_id, bucket_id, schedule_id) from a Basecamp schedule URL."""
    try:
        parsed = URL(url.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.host not in _SCHEDULE_HOSTS:
        return None
    if (match := _SCHEDULE_PATH.match(parsed.path)) is None:
        return None
    account_id, bucket_id, schedule_id = (int(part) for part in match.groups())
    return account_id, bucket_id, schedule_id


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Basecamp Out of Office OAuth2 config flow."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._accounts: dict[int, str] = {}
        self._account_id: int | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Continue after OAuth: verify identity, then pick account and schedule."""
        self._data = data

        if self.source == SOURCE_REAUTH:
            return await self._async_finish_reauth()

        try:
            accounts = await self._async_fetch_accounts()
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError):
            _LOGGER.exception("Failed to fetch Launchpad identity")
            return self.async_abort(reason="cannot_connect")

        if not accounts:
            return self.async_abort(reason="no_accounts")
        self._accounts = accounts

        if len(accounts) == 1:
            self._account_id = next(iter(accounts))
            return await self.async_step_schedule()
        return await self.async_step_account()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a Basecamp account."""
        if user_input is not None:
            self._account_id = int(user_input[CONF_ACCOUNT_ID])
            return await self.async_step_schedule()

        options = [
            SelectOptionDict(value=str(account_id), label=name)
            for account_id, name in self._accounts.items()
        ]
        return self.async_show_form(
            step_id="account",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ACCOUNT_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the URL of the time-off schedule."""
        assert self._account_id is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            parsed = parse_schedule_url(user_input[CONF_SCHEDULE_URL])
            if parsed is None:
                errors[CONF_SCHEDULE_URL] = "invalid_url"
            elif parsed[0] != self._account_id:
                errors[CONF_SCHEDULE_URL] = "account_mismatch"
            else:
                _, bucket_id, schedule_id = parsed
                try:
                    person_id = await self._async_fetch_person_id(self._account_id)
                except BasecampAuthError:
                    errors["base"] = "invalid_auth"
                except BasecampApiError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(f"{self._account_id}-{person_id}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=self._accounts[self._account_id],
                        data={
                            **self._data,
                            CONF_ACCOUNT_ID: self._account_id,
                            CONF_BUCKET_ID: bucket_id,
                            CONF_SCHEDULE_ID: schedule_id,
                            CONF_PERSON_ID: person_id,
                        },
                    )

        return self.async_show_form(
            step_id="schedule",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SCHEDULE_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"account": self._accounts[self._account_id]},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the token stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def _async_finish_reauth(self) -> ConfigFlowResult:
        """Update the token on the existing entry if it is the same person."""
        entry = self._get_reauth_entry()
        account_id = entry.data[CONF_ACCOUNT_ID]
        try:
            person_id = await self._async_fetch_person_id(account_id)
        except BasecampAuthError:
            return self.async_abort(reason="invalid_auth")
        except BasecampApiError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(f"{account_id}-{person_id}")
        self._abort_if_unique_id_mismatch(reason="wrong_account")
        return self.async_update_reload_and_abort(
            entry, data_updates=self._data
        )

    async def _async_fetch_accounts(self) -> dict[int, str]:
        """Return Basecamp 3/4 accounts of the authorized identity as {id: name}."""
        session = async_get_clientsession(self.hass)
        async with session.get(
            LAUNCHPAD_IDENTITY,
            headers={
                "Authorization": f"Bearer {self._data['token']['access_token']}",
                "User-Agent": USER_AGENT,
            },
        ) as resp:
            resp.raise_for_status()
            body = await resp.json()
        return {
            int(account["id"]): account["name"]
            for account in body.get("accounts", [])
            if account.get("product") == "bc3"
        }

    async def _async_fetch_person_id(self, account_id: int) -> int:
        """Return the authorized user's person ID in the account."""

        async def token_getter() -> str:
            return self._data["token"]["access_token"]

        client = BasecampClient(
            async_get_clientsession(self.hass), token_getter, account_id
        )
        return await client.get_person_id()


class OptionsFlowHandler(OptionsFlow):
    """Options for Basecamp Out of Office."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the run time."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_RUN_TIME,
                        default=self.config_entry.options.get(
                            CONF_RUN_TIME, DEFAULT_RUN_TIME
                        ),
                    ): TimeSelector()
                }
            ),
        )
