"""OAuth2 implementation for 37signals Launchpad."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import OAuth2TokenRequestConnectionError
from homeassistant.helpers import config_entry_oauth2_flow


class LaunchpadOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """Launchpad's pre-standard OAuth2 dialect.

    Launchpad uses ``type=web_server`` / ``type=refresh`` instead of
    ``grant_type``, and its refresh response omits ``refresh_token``.
    """

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data appended to the authorize URL."""
        return {"type": "web_server"}

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Exchange the authorization code for tokens."""
        return await self._token_request(
            {
                "type": "web_server",
                "redirect_uri": external_data["state"]["redirect_uri"],
                "code": external_data["code"],
            }
        )

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh the access token, keeping the old refresh token if none is returned.

        ``expires_at`` is computed by the base class ``async_refresh_token``.
        """
        new_token = await self._token_request(
            {"type": "refresh", "refresh_token": token["refresh_token"]}
        )
        # Merging a response without an access token would keep the stale one
        # while extending its expiry.
        if not new_token.get("access_token"):
            raise OAuth2TokenRequestConnectionError(domain=self.service_domain)
        return {**token, **new_token}
