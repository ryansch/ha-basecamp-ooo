"""Tests for the basecamp_ooo config flow."""

from collections.abc import Generator
from unittest.mock import ANY, patch

import pytest

from homeassistant import config_entries
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.basecamp_ooo.api import BasecampApiError
from custom_components.basecamp_ooo.config_flow import parse_schedule_url
from custom_components.basecamp_ooo.const import (
    CONF_ACCOUNT_ID,
    CONF_BUCKET_ID,
    CONF_PERSON_ID,
    CONF_RUN_TIME,
    CONF_SCHEDULE_ID,
    DEFAULT_RUN_TIME,
    DOMAIN,
    LAUNCHPAD_IDENTITY,
    OAUTH_AUTHORIZE,
    OAUTH_TOKEN,
    USER_AGENT,
)

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
REDIRECT_URI = "https://example.com/auth/external/callback"

ACCOUNT_ID = 9990001
BUCKET_ID = 9990002
SCHEDULE_ID = 9990003
PERSON_ID = 9990004
IDENTITY_ID = 9999999  # Launchpad identity id; must never be used as the person id
SCHEDULE_URL = (
    f"https://3.basecamp.com/{ACCOUNT_ID}/buckets/{BUCKET_ID}/schedules/{SCHEDULE_ID}"
)

TOKEN_RESPONSE = {
    "access_token": "mock-access-token",
    "refresh_token": "mock-refresh-token",
    "expires_in": 1209600,
}

BC3_ACCOUNT = {
    "product": "bc3",
    "id": ACCOUNT_ID,
    "name": "Acme Corp",
    "href": f"https://3.basecampapi.com/{ACCOUNT_ID}",
    "app_href": f"https://3.basecamp.com/{ACCOUNT_ID}",
}
OTHER_BC3_ACCOUNT = {
    "product": "bc3",
    "id": 1111111,
    "name": "Side Project",
    "href": "https://3.basecampapi.com/1111111",
    "app_href": "https://3.basecamp.com/1111111",
}
BC2_ACCOUNT = {"product": "bcx", "id": 2222222, "name": "Classic"}


def identity(*accounts: dict) -> dict:
    """Return a Launchpad authorization.json body."""
    return {
        "expires_at": "2026-11-24T00:00:00Z",
        "identity": {"id": IDENTITY_ID, "first_name": "Test", "last_name": "User"},
        "accounts": list(accounts),
    }


@pytest.fixture(autouse=True)
async def setup_credentials(hass: HomeAssistant) -> None:
    """Register application credentials for the integration."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


class FakeBasecampClient:
    """Stand-in for BasecampClient; records the token the flow hands it.

    The aiohttp test mocker cannot serve BasecampClient (no ``links`` on its
    responses); the client itself is covered by tests/test_api.py.
    """

    person_id: int | Exception = PERSON_ID
    calls: list[tuple[int, str]] = []

    def __init__(self, session, token_getter, account_id: int) -> None:
        self._token_getter = token_getter
        self._account_id = account_id

    async def get_person_id(self) -> int:
        FakeBasecampClient.calls.append((self._account_id, await self._token_getter()))
        if isinstance(self.person_id, Exception):
            raise self.person_id
        return self.person_id


@pytest.fixture(autouse=True)
def mock_client() -> Generator[type[FakeBasecampClient]]:
    """Replace BasecampClient in the config flow."""
    FakeBasecampClient.person_id = PERSON_ID
    FakeBasecampClient.calls = []
    with patch(
        "custom_components.basecamp_ooo.config_flow.BasecampClient", FakeBasecampClient
    ):
        yield FakeBasecampClient


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[None]:
    """Keep the integration from actually setting up."""
    with patch("custom_components.basecamp_ooo.async_setup_entry", return_value=True):
        yield


async def _complete_oauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    result: dict,
) -> dict:
    """Drive the external OAuth step and return the next flow result."""
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI}
    )
    assert result["url"].startswith(f"{OAUTH_AUTHORIZE}?")
    assert "type=web_server" in result["url"]

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(OAUTH_TOKEN, json=TOKEN_RESPONSE)
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def _start_user_flow(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)


def test_parse_schedule_url() -> None:
    """Schedule URLs on 3.basecamp.com and app.basecamp.com parse to IDs."""
    expected = (ACCOUNT_ID, BUCKET_ID, SCHEDULE_ID)
    assert parse_schedule_url(SCHEDULE_URL) == expected
    assert (
        parse_schedule_url(
            f"https://app.basecamp.com/{ACCOUNT_ID}/buckets/{BUCKET_ID}/schedules/{SCHEDULE_ID}"
        )
        == expected
    )
    assert parse_schedule_url(f"  {SCHEDULE_URL}/  ") == expected
    assert parse_schedule_url(f"{SCHEDULE_URL}?foo=bar") == expected
    assert parse_schedule_url("https://example.com/1/buckets/2/schedules/3") is None
    assert parse_schedule_url(f"https://3.basecamp.com/{ACCOUNT_ID}/projects/1") is None
    assert parse_schedule_url("not a url") is None
    assert parse_schedule_url("") is None


@pytest.mark.usefixtures("current_request_with_host")
async def test_full_flow_creates_entry(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """One bc3 account skips the account step and creates the entry."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC3_ACCOUNT, BC2_ACCOUNT))

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": SCHEDULE_URL}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme Corp"
    assert result["result"].unique_id == f"{ACCOUNT_ID}-{PERSON_ID}"
    assert result["data"] == {
        "auth_implementation": DOMAIN,
        "token": {**TOKEN_RESPONSE, "expires_at": ANY},
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        CONF_BUCKET_ID: BUCKET_ID,
        CONF_SCHEDULE_ID: SCHEDULE_ID,
        CONF_PERSON_ID: PERSON_ID,
    }

    identity_call = next(c for c in aioclient_mock.mock_calls if str(c[1]) == LAUNCHPAD_IDENTITY)
    assert identity_call[3]["Authorization"] == "Bearer mock-access-token"
    assert identity_call[3]["User-Agent"] == USER_AGENT
    assert FakeBasecampClient.calls == [(ACCOUNT_ID, "mock-access-token")]


@pytest.mark.usefixtures("current_request_with_host")
async def test_multiple_accounts_shows_account_step(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Several bc3 accounts ask the user to pick one first."""
    aioclient_mock.get(
        LAUNCHPAD_IDENTITY, json=identity(OTHER_BC3_ACCOUNT, BC3_ACCOUNT, BC2_ACCOUNT)
    )

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_ID: str(ACCOUNT_ID)}
    )
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": SCHEDULE_URL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme Corp"
    assert result["data"][CONF_ACCOUNT_ID] == ACCOUNT_ID


@pytest.mark.usefixtures("current_request_with_host")
async def test_no_bc3_accounts_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An identity with no Basecamp 3/4 account aborts."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC2_ACCOUNT))

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_accounts"


@pytest.mark.usefixtures("current_request_with_host")
async def test_schedule_url_for_other_account_errors(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A schedule URL from a different account is rejected."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC3_ACCOUNT))

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"schedule_url": f"https://3.basecamp.com/1111111/buckets/{BUCKET_ID}/schedules/{SCHEDULE_ID}"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule"
    assert result["errors"] == {"schedule_url": "account_mismatch"}


@pytest.mark.usefixtures("current_request_with_host")
async def test_invalid_schedule_url_errors(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A URL that is not a schedule URL is rejected, and the flow can recover."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC3_ACCOUNT))

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": "https://example.com/nope"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"schedule_url": "invalid_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": SCHEDULE_URL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("current_request_with_host")
async def test_profile_error_shows_cannot_connect(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A failing profile lookup keeps the user on the schedule step."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC3_ACCOUNT))
    FakeBasecampClient.person_id = BasecampApiError("boom")

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": SCHEDULE_URL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("current_request_with_host")
async def test_identity_error_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A failing Launchpad identity request aborts the flow."""
    aioclient_mock.get(LAUNCHPAD_IDENTITY, status=500)

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.usefixtures("current_request_with_host")
async def test_duplicate_entry_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The same account and person can only be configured once."""
    MockConfigEntry(domain=DOMAIN, unique_id=f"{ACCOUNT_ID}-{PERSON_ID}").add_to_hass(
        hass
    )
    aioclient_mock.get(LAUNCHPAD_IDENTITY, json=identity(BC3_ACCOUNT))

    result = await _start_user_flow(hass, hass_client_no_auth, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": SCHEDULE_URL}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _existing_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Corp",
        unique_id=f"{ACCOUNT_ID}-{PERSON_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "old-refresh-token",
                "expires_in": 1209600,
                "expires_at": 0,
            },
            CONF_ACCOUNT_ID: ACCOUNT_ID,
            CONF_BUCKET_ID: BUCKET_ID,
            CONF_SCHEDULE_ID: SCHEDULE_ID,
            CONF_PERSON_ID: PERSON_ID,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("current_request_with_host")
async def test_reauth_updates_token(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reauth replaces the token and keeps the rest of the entry data."""
    entry = _existing_entry(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"]["access_token"] == "mock-access-token"
    assert entry.data["token"]["refresh_token"] == "mock-refresh-token"
    assert entry.data["auth_implementation"] == DOMAIN
    assert entry.data[CONF_SCHEDULE_ID] == SCHEDULE_ID
    assert entry.data[CONF_PERSON_ID] == PERSON_ID
    assert entry.data[CONF_ACCOUNT_ID] == ACCOUNT_ID
    assert FakeBasecampClient.calls == [(ACCOUNT_ID, "mock-access-token")]


@pytest.mark.usefixtures("current_request_with_host")
async def test_reauth_with_other_user_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reauthorizing as a different Basecamp person leaves the entry unchanged."""
    entry = _existing_entry(hass)
    FakeBasecampClient.person_id = 12345

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["token"]["access_token"] == "old-access-token"


async def test_options_flow_sets_run_time(hass: HomeAssistant) -> None:
    """The options flow stores the run time, defaulting to 06:00:00."""
    entry = _existing_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema_keys = {str(k): k for k in result["data_schema"].schema}
    assert schema_keys[CONF_RUN_TIME].default() == DEFAULT_RUN_TIME

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_RUN_TIME: "07:30:00"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_RUN_TIME: "07:30:00"}
