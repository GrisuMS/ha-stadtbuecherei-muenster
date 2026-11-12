"""The Stadtbücherei Münster integration."""

from __future__ import annotations

import aiohttp
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import StadtbuechereiClient
from .coordinator import StadtbuechereiConfigEntry, StadtbuechereiCoordinator
from .entity import account_device_info

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

    # Loan devices link to the account device by its registry id.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **account_device_info(entry)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StadtbuechereiConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
