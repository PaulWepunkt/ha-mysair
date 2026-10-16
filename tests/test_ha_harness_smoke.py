"""P2 smoke test: checks that the Home Assistant harness discovers custom_components/mysair.

Only runs with pytest-homeassistant-custom-component installed (see
Dockerfile.test); skipped in the P0/P1 environment without HA
(pytest.importorskip).
"""

import pytest

pytest.importorskip("homeassistant")

from homeassistant.loader import async_get_custom_components


async def test_mysair_is_discovered_as_custom_component(
    hass, enable_custom_integrations
):
    components = await async_get_custom_components(hass)
    assert "mysair" in components
