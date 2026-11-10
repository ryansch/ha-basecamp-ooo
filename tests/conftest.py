"""Fixtures for basecamp_ooo tests."""

import pytest

# Import our integration package before any test creates `hass`. HA's loader
# temporarily prepends the test config dir (which ships its own
# `custom_components` package) to sys.path and then does
# `import custom_components`; importing ours first caches it in sys.modules so
# the loader finds basecamp_ooo.
import custom_components.basecamp_ooo  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in every test."""
    yield
