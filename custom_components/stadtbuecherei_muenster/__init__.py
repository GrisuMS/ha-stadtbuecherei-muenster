"""The Stadtbücherei Münster integration."""

from __future__ import annotations

import aiohttp
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import StadtbuechereiClient
from .coordinator import StadtbuechereiConfigEntry, StadtbuechereiCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.IMAGE, Platform.SENSOR]


def create_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Own session per account, so login cookies are not shared.

    Cookies must be sent back unquoted: the catalogue's base64 login cookie
    (COPAUTH) would otherwise arrive as "..." and the server logs out.
    """
    return async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(quote_cookie=False)
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: StadtbuechereiConfigEntry
) -> bool:
    """Set up the library account from a config entry."""
    client = StadtbuechereiClient(
        create_session(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
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
