"""The Stadtbücherei Münster integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import StadtbuechereiClient
from .coordinator import StadtbuechereiConfigEntry, StadtbuechereiCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.IMAGE, Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: StadtbuechereiConfigEntry
) -> bool:
    """Set up the library account from a config entry."""
    # Own session so the ASP.NET session cookie is not shared with other accounts.
    session = async_create_clientsession(hass)
    client = StadtbuechereiClient(
        session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )
    coordinator = StadtbuechereiCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StadtbuechereiConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
