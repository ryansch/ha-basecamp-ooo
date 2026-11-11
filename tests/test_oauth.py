"""Tests for the Launchpad OAuth2 implementation."""

import time

import pytest
from yarl import URL

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.basecamp_ooo.const import DOMAIN, OAUTH_AUTHORIZE, OAUTH_TOKEN
from custom_components.basecamp_ooo.oauth import LaunchpadOAuth2Implementation

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
REDIRECT_URI = "https://example.com/auth/external/callback"


@pytest.fixture
def impl(hass: HomeAssistant) -> LaunchpadOAuth2Implementation:
    """Return a Launchpad OAuth2 implementation."""
    return LaunchpadOAuth2Implementation(
        hass, DOMAIN, CLIENT_ID, CLIENT_SECRET, OAUTH_AUTHORIZE, OAUTH_TOKEN
    )


@pytest.mark.usefixtures("current_request_with_host")
async def test_authorize_url_includes_type_web_server(
    impl: LaunchpadOAuth2Implementation,
) -> None:
    """The authorize URL targets Launchpad and carries type=web_server."""
    url = URL(await impl.async_generate_authorize_url("flow-1"))

    assert str(url.with_query(None)) == OAUTH_AUTHORIZE
    assert url.query["type"] == "web_server"
    assert url.query["client_id"] == CLIENT_ID
    assert url.query["redirect_uri"] == REDIRECT_URI
    assert "state" in url.query


async def test_token_exchange_posts_type_web_server_and_code(
    impl: LaunchpadOAuth2Implementation, aioclient_mock: AiohttpClientMocker
) -> None:
    """The code exchange posts Launchpad's web_server params."""
    aioclient_mock.post(
        OAUTH_TOKEN,
        json={"access_token": "at", "refresh_token": "rt", "expires_in": 1209600},
    )

    token = await impl.async_resolve_external_data(
        {"code": "abcd", "state": {"redirect_uri": REDIRECT_URI}}
    )

    assert token == {"access_token": "at", "refresh_token": "rt", "expires_in": 1209600}
    assert aioclient_mock.call_count == 1
    _, url, data, _ = aioclient_mock.mock_calls[0]
    assert str(url) == OAUTH_TOKEN
    assert data == {
        "type": "web_server",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": "abcd",
    }


async def test_refresh_posts_type_refresh_and_keeps_old_refresh_token_when_omitted(
    impl: LaunchpadOAuth2Implementation, aioclient_mock: AiohttpClientMocker
) -> None:
    """Refresh posts type=refresh; a response without refresh_token keeps the old one."""
    aioclient_mock.post(OAUTH_TOKEN, json={"access_token": "new-at", "expires_in": 3600})
    old = {
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "expires_in": 3600,
        "expires_at": time.time() - 10,
    }

    before = time.time()
    token = await impl.async_refresh_token(old)

    _, url, data, _ = aioclient_mock.mock_calls[0]
    assert str(url) == OAUTH_TOKEN
    assert data == {
        "type": "refresh",
        "refresh_token": "old-rt",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert token["access_token"] == "new-at"
    assert token["refresh_token"] == "old-rt"
    assert token["expires_in"] == 3600
    assert before + 3600 <= token["expires_at"] <= time.time() + 3600


async def test_refresh_uses_new_refresh_token_when_returned(
    impl: LaunchpadOAuth2Implementation, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rotated refresh_token in the response replaces the old one."""
    aioclient_mock.post(
        OAUTH_TOKEN,
        json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
    )

    token = await impl.async_refresh_token(
        {"access_token": "old-at", "refresh_token": "old-rt", "expires_at": 0}
    )

    assert token["refresh_token"] == "new-rt"
