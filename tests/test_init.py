"""Tests for the basecamp_ooo integration setup."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.basecamp_ooo.const import DOMAIN


async def test_manifest_loads(hass: HomeAssistant) -> None:
    """The manifest declares a config flow and depends on application_credentials."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == "basecamp_ooo"
    assert integration.config_flow is True
    assert "application_credentials" in integration.dependencies
