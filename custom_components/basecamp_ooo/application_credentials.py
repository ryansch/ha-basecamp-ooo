"""Application credentials platform for Basecamp Out of Office."""

from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant

from .const import OAUTH_AUTHORIZE, OAUTH_TOKEN
from .oauth import LaunchpadOAuth2Implementation


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> LaunchpadOAuth2Implementation:
    """Return the Launchpad auth implementation."""
    return LaunchpadOAuth2Implementation(
        hass,
        auth_domain,
        credential.client_id,
        credential.client_secret,
        OAUTH_AUTHORIZE,
        OAUTH_TOKEN,
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    return {
        "integrations_url": "https://launchpad.37signals.com/integrations",
        "redirect_url": "https://my.home-assistant.io/redirect/oauth",
    }
